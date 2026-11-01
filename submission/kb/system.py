"""KnowledgeSystem orchestrator. Owns all glue between stages; stage tracks never edit this file."""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from contract import Answer, Conflict, Snapshot, Source

from . import classify, compose, conflicts as conflicts_mod, intent, normalize, resolve
from .extract import llm_facts, regex, tables
from .llm import LLM
from .model import PageContext
from .retrieval.pipeline import Retriever
from .snapshots import SnapshotRegistry, effective_as_of
from .store import Store

ROOT = Path(__file__).resolve().parent.parent  # submission/
CACHE_DIR = ROOT / ".cache"


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

    def ingest(self, snapshot: Snapshot) -> None:
        sid = snapshot.snapshot_id
        self.registry.add(sid, snapshot.taken_on)
        self.store.add_snapshot(sid, snapshot.taken_on)
        bp = normalize.fit_boilerplate(snapshot.pages)
        for page in sorted(snapshot.pages, key=lambda p: p.url):
            info = classify.classify_page(page.url, page.title, page.markdown, snapshot.taken_on)
            md = normalize.clean(page.markdown, bp)
            page_id = self.store.add_page(sid, page.url, page.title, info, hashlib.sha256(md.encode("utf-8")).hexdigest())
            stored = self.store.add_chunks(sid, page_id, normalize.chunk_page(page.url, page.title, md))
            ctx = PageContext(sid, snapshot.taken_on, page.url, page.title, info, md, stored)
            try:
                obs = tables.extract(ctx) + regex.extract(ctx) + llm_facts.extract(ctx, self.llm)
            except Exception:
                continue  # one bad page must not abort ingest
            self.store.add_facts(obs)
        self.store.set_resolved(sid, resolve.resolve_snapshot(self.store, sid))
        self.retriever.index_snapshot(sid)
        self._conflicts = None

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        try:
            return self._answer(question, as_of)
        except Exception:
            try:
                return compose.refusal(question)
            except Exception:
                return Answer(text="That isn't listed on the website, so I can't confirm it. "
                                   "Please contact Dupaco directly for this information.", confident=False)

    def _answer(self, question: str, as_of: Optional[str]) -> Answer:
        sid = self.registry.resolve(effective_as_of(question, as_of))
        it = intent.parse(question, self.llm)
        memo = self.store.memo_get(it.canonical, sid)
        if memo is not None:
            return _answer_from_dict(memo)
        sel = resolve.select_for_intent(it, self.store.resolved(sid, it.product, it.attribute)) if it.product else []
        if sel:
            ans = compose.render_facts(question, it, sel, sid)
        else:
            picked = self.retriever.pick(it.cleaned_question, self.retriever.search(it.cleaned_question, sid))
            ans = compose.render_prose(question, it, picked, sid, self.llm) if picked else compose.refusal(question)
        ans = compose.sanitize(ans, keep={r.value for r in sel}, drop=resolve.stale_values(self.store, sel))
        self.store.memo_put(it.canonical, sid, asdict(ans))
        return ans

    def conflicts(self) -> list[Conflict]:
        if self._conflicts is None:
            self._conflicts = conflicts_mod.build_conflicts(self.store, self.registry)
        return self._conflicts


def build() -> KnowledgeSystem:
    db = db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    return KnowledgeSystem(db, llm_cache_path())
