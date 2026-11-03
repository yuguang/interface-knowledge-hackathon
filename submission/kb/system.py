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

from . import classify, compose, conflicts as conflicts_mod, intent, normalize, resolve, temporal, tracing
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
        tracing.annotate(**{f"failed_{stage}": True})
        tracing.mark_failed(f"stage {stage} failed ({context[:80]})")
        log.error("stage %s failed (%s)\n%s", stage, context, traceback.format_exc())

    @tracing.traced("kb.ingest", "chain", inputs=lambda d: {
        "snapshot_id": d["snapshot"].snapshot_id, "taken_on": d["snapshot"].taken_on,
        "n_pages": len(d["snapshot"].pages)})
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
        pages = sorted(snapshot.pages, key=lambda p: p.url)
        for i, page in enumerate(pages, 1):
            try:
                with tracing.span("kb.ingest_page", "chain", inputs={"snapshot_id": sid, "url": page.url},
                                  metadata={"step_number": i, "total_steps": len(pages)}) as run:
                    run.end(outputs={"facts": self._ingest_page(sid, snapshot.taken_on, page, bp)})
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

    def _ingest_page(self, sid: str, taken_on: str, page, bp: frozenset) -> dict:
        """Returns the number of facts each extractor produced (logged on the trace)."""
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
        try:  # link targets for prose answers ("where do I join online"); clean() keeps only link text
            self.store.add_links(sid, page.url, normalize.page_links(page.markdown))
        except Exception:
            self._fail("links", page.url)
        page_id = self.store.add_page(sid, page.url, page.title, info, hashlib.sha256(md.encode("utf-8")).hexdigest())
        stored = self.store.add_chunks(sid, page_id, chunks)
        ctx = PageContext(sid, taken_on, page.url, page.title, info, md, stored)
        obs = []
        counts: dict = {"chunks": len(chunks)}
        for name, fn in (("tables", lambda: tables.extract(ctx)), ("regex", lambda: regex.extract(ctx)),
                         ("llm_facts", lambda: llm_facts.extract(ctx, self.llm))):
            try:
                got = fn()
                counts[name] = len(got)
                obs += got
            except Exception:  # one failing extractor must not abort ingest or drop the others' facts
                self._fail(name, page.url)
                counts[name] = "failed"
        self.store.add_facts(obs)
        return counts

    def _refusal(self, question: str) -> Answer:
        try:
            return compose.refusal(question)
        except Exception:
            self._fail("refusal", question[:80])
            return Answer(text=_REFUSAL_TEXT, confident=False)

    @tracing.traced("kb.answer", "chain", tags=["answer"])
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
        tracing.annotate(snapshot_id=sid, as_of=as_of)
        try:
            it = intent.parse(question, self.llm)
        except Exception:
            self._fail("intent", q80)
            degraded = True
            it = Intent("prose", None, None, {}, None, question, question.lower().strip())
        try:  # the reporting year joins the intent (and so the memo key); it never changes the snapshot chosen above
            period = temporal.resolve_reporting_period(question, as_of, self.registry.taken_on(self.registry.latest()))
            it = temporal.apply_reporting_period(it, period)
            tracing.annotate(reference_date=period.reference_date, reporting_year=period.year)
        except Exception:
            self._fail("temporal", q80)
            degraded = True
        tracing.annotate(intent_family=it.family, product=it.product, attribute=it.attribute,
                         canonical=it.canonical)
        memo = self.store.memo_get(it.canonical, sid)
        if memo is not None:
            tracing.annotate(path="memo", degraded=degraded)
            return _answer_from_dict(memo)
        sel, cands = [], []
        if it.product:
            try:
                # every product the intent spans (a certificate scope covers regular and special), in a fixed order
                for prod in resolve.products_for_intent(it):
                    cands += self.store.resolved(sid, prod, it.attribute)
                sel = resolve.select_for_intent(it, cands)
            except Exception:
                self._fail("select", q80)
                degraded = True
                sel = []
        ans = None
        from_facts = False
        ambiguous: Optional[list] = None
        if sel:
            try:  # several subjects matched and the question names none: let retrieval complete the answer
                ambiguous = resolve.subject_split(it, sel)
            except Exception:
                self._fail("subject_split", q80)
        if sel and ambiguous is None:
            try:
                ans = compose.render_facts(question, it, sel, sid)
                from_facts = True
            except Exception:  # fall through to the retrieval path
                self._fail("render_facts", q80)
                degraded = True
        if ans is None and not sel and not degraded and temporal.is_vto_count(it):
            # no VTO count fact (or not for the requested year): refuse, never answer from nearby prose
            ans = self._refusal(question)
        if ans is None:
            try:
                rq = it.cleaned_question
                if ambiguous:  # on-subject facts name what to look for ("charter area"): widen the search with them
                    rq = f"{rq} {' '.join(r.value for r in ambiguous)}"
                picked = self.retriever.pick(rq, self.retriever.search(rq, sid))
                if ambiguous:  # the rest of each fact's own page (the states of a charter area sit in another section)
                    seen_ids = {h.chunk_id for h in picked}
                    budget = 6000
                    for r in ambiguous[:2]:
                        for h in self.store.page_chunks(sid, r.winner.url):
                            if h.chunk_id not in seen_ids and budget > 0:
                                picked.append(h)
                                budget -= len(h.text)
                links = {h.url: self.store.links(sid, h.url) for h in picked}
                dates = self.store.page_dates(sid, [h.url for h in picked])
                ans = compose.render_prose(question, it, picked, sid, self.llm, links=links, dates=dates,
                                           facts=ambiguous, limit=24 if ambiguous else 4) if (picked or ambiguous) else self._refusal(question)
            except Exception:
                self._fail("retrieval", q80)
                degraded = True
                ans = self._refusal(question)
        try:
            # Prose answers about a known product/attribute (I8): strip values stale for any of its candidate keys.
            basis = sel if from_facts else (ambiguous or resolve.matching(it, cands))
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
        tracing.annotate(path="facts" if from_facts else ("refusal" if not ans.confident else "retrieval"),
                         degraded=degraded, n_selected_facts=len(sel), confident=ans.confident)
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
