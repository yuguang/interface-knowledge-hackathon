"""KnowledgeSystem orchestrator. Owns all glue between stages; stage tracks never edit this file."""
from __future__ import annotations

import collections
import hashlib
import logging
import os
import re
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from contract import Answer, Conflict, Snapshot, Source

from . import classify, compose, conflicts as conflicts_mod, intent, normalize, resolve
from .extract import llm_facts, regex, tables
from .llm import LLM
from .model import AUTHORITY, Chunk, Intent, PageContext, PageInfo, chunk_hash
from .retrieval.pipeline import Retriever
from .snapshots import SnapshotRegistry, effective_as_of
from .store import Store

ROOT = Path(__file__).resolve().parent.parent  # submission/
CACHE_DIR = ROOT / ".cache"
log = logging.getLogger("kb")
_REFUSAL_TEXT = ("That isn't listed on the website, so I can't confirm it. "
                 "Please contact Dupaco directly for this information.")


def db_path() -> Path:
    return Path(os.environ.get("KB_DB_PATH") or CACHE_DIR / "kb.sqlite")


def llm_cache_path() -> Path:
    return Path(os.environ.get("KB_LLM_CACHE") or CACHE_DIR / "llm.sqlite")


def _answer_from_dict(d: dict) -> Answer:
    d = dict(d)
    d["sources"] = [Source(**s) for s in d.get("sources", [])]
    return Answer(**d)


class KnowledgeSystem:
    def __init__(self, db: Path, llm_cache: Path):
        self.registry = SnapshotRegistry()
        self.store = Store(str(db))
        self.llm = LLM(llm_cache)
        self.retriever = Retriever(self.store, self.llm, Path(db).parent / "index")
        self._conflicts: Optional[list[Conflict]] = None
        self.stage_errors: collections.Counter = collections.Counter()

    def _fail(self, stage: str, context: str) -> None:
        self.stage_errors[stage] += 1
        log.error("stage %s failed (%s)\n%s", stage, context, traceback.format_exc())

    def ingest(self, snapshot: Snapshot) -> None:
        """Fail-soft per stage (R14): the harness does not guard ingest, so one bad page never aborts it."""
        sid = snapshot.snapshot_id
        self.registry.add(sid, snapshot.taken_on)
        self.store.add_snapshot(sid, snapshot.taken_on)
        try:
            bp = normalize.fit_boilerplate(snapshot.pages)
        except Exception:
            self._fail("fit_boilerplate", sid)
            bp = frozenset()
        for page in sorted(snapshot.pages, key=lambda p: p.url):
            try:
                self._ingest_page(sid, snapshot.taken_on, page, bp)
            except Exception:  # last resort: a store-level failure on one page skips only that page
                self._fail("page", page.url)
        try:
            resolved = resolve.resolve_snapshot(self.store, sid)
        except Exception:
            self._fail("resolve", sid)
            resolved = []
        self.store.set_resolved(sid, resolved)
        try:
            self.retriever.index_snapshot(sid)
        except Exception:  # FTS search still works without the extra index
            self._fail("index_snapshot", sid)
        self._conflicts = None

    def _ingest_page(self, sid: str, taken_on: str, page, bp: frozenset) -> None:
        try:
            info = classify.classify_page(page.url, page.title, page.markdown, taken_on)
        except Exception:
            self._fail("classify", page.url)
            info = PageInfo("other", AUTHORITY["other"], None)
        try:
            md = normalize.clean(page.markdown, bp)
        except Exception:
            self._fail("clean", page.url)
            md = page.markdown
        try:
            chunks = normalize.chunk_page(page.url, page.title, md)
        except Exception:
            self._fail("chunk", page.url)
            chunks = _fallback_chunks(page.url, page.title, md)
        page_id = self.store.add_page(sid, page.url, page.title, info, hashlib.sha256(md.encode("utf-8")).hexdigest())
        stored = self.store.add_chunks(sid, page_id, chunks)
        ctx = PageContext(sid, taken_on, page.url, page.title, info, md, stored)
        obs = []
        for name, fn in (("tables", lambda: tables.extract(ctx)), ("regex", lambda: regex.extract(ctx)),
                         ("llm_facts", lambda: llm_facts.extract(ctx, self.llm))):
            try:
                obs += fn()
            except Exception:  # one failing extractor must not abort ingest or drop the others' facts
                self._fail(name, page.url)
        self.store.add_facts(obs)

    def _refusal(self, question: str) -> Answer:
        try:
            return compose.refusal(question)
        except Exception:
            self._fail("refusal", question[:80])
            return Answer(text=_REFUSAL_TEXT, confident=False)

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        try:
            return self._answer(question, as_of)
        except Exception:  # last resort; each stage below already degrades on its own
            self._fail("answer", question[:80])
            return self._refusal(question)

    def _answer(self, question: str, as_of: Optional[str]) -> Answer:
        """Each stage degrades on its own (R14); a degraded answer is not memoised."""
        q80 = question[:80]
        degraded = False
        sid = self.registry.resolve(effective_as_of(question, as_of))
        try:
            it = intent.parse(question, self.llm)
        except Exception:
            self._fail("intent", q80)
            degraded = True
            it = Intent("prose", None, None, {}, None, question, question.lower().strip())
        memo = self.store.memo_get(it.canonical, sid)
        if memo is not None:
            return _answer_from_dict(memo)
        sel, cands = [], []
        if it.product:
            try:
                cands = self.store.resolved(sid, it.product, it.attribute)
                sel = resolve.select_for_intent(it, cands)
            except Exception:
                self._fail("select", q80)
                degraded = True
                sel = []
        ans = None
        from_facts = False
        if sel:
            try:
                ans = compose.render_facts(question, it, sel, sid)
                from_facts = True
            except Exception:  # fall through to the retrieval path
                self._fail("render_facts", q80)
                degraded = True
        if ans is None:
            try:
                picked = self.retriever.pick(it.cleaned_question, self.retriever.search(it.cleaned_question, sid))
                ans = compose.render_prose(question, it, picked, sid, self.llm) if picked else self._refusal(question)
            except Exception:
                self._fail("retrieval", q80)
                degraded = True
                ans = self._refusal(question)
        try:
            # Prose answers about a known product/attribute (I8): strip values stale for any of its candidate keys.
            basis = sel if from_facts else resolve.matching(it, cands)
            drop = resolve.stale_values(self.store, basis)
        except Exception:
            self._fail("stale_values", q80)
            degraded = True
            drop = set()
        try:
            ans = compose.sanitize(ans, keep={r.value for r in sel}, drop=drop)
        except Exception:  # can't strip stale values: refuse rather than risk stating one
            self._fail("sanitize", q80)
            degraded = True
            if drop:
                ans = self._refusal(question)
        if not degraded:
            self.store.memo_put(it.canonical, sid, asdict(ans))
        return ans

    def conflicts(self) -> list[Conflict]:
        if self._conflicts is None:
            try:
                self._conflicts = conflicts_mod.build_conflicts(self.store, self.registry)
            except Exception:  # not cached: a later call retries
                self._fail("conflicts", "build_conflicts")
                return []
        return self._conflicts


def _fallback_chunks(url: str, title: str, cleaned: str) -> list[Chunk]:
    """Paragraph chunker used when the real chunker fails (same behaviour as the Stage 1 stub)."""
    out = []
    for para in re.split(r"\n\s*\n", cleaned):
        text = para.strip()
        if text:
            out.append(Chunk(chunk_hash(url, text), url, title, "", text))
    return out


def build() -> KnowledgeSystem:
    db = db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    return KnowledgeSystem(db, llm_cache_path())
