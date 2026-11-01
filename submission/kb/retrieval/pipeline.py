"""Hybrid retriever: Haystack pipeline of FTS5 + Chroma, fused by reciprocal rank fusion, then an LLM picker."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "False")

from ..llm import LLM
from ..model import Hit
from ..store import Store
from . import fts
from .dense import DenseIndex
from .picker import pick_sections

log = logging.getLogger("kb")
CANDIDATES = 30  # per retriever, before fusion
MAX_PICKED = 2
RRF_TOP = 1.0  # Haystack's normalised RRF score of a section ranked first by every retriever that found something
RRF_K = 61  # Haystack DocumentJoiner's RRF constant


def _build_pipeline(store: Store, dense: DenseIndex):
    from haystack import Document, Pipeline, component
    from haystack.components.joiners import DocumentJoiner

    def docs(scored):
        return [Document(id=str(cid), content="", score=s, meta={"chunk_id": cid}) for cid, s in scored]

    @component
    class Fts5Retriever:
        @component.output_types(documents=list[Document])
        def run(self, query: str, snapshot_id: str, top_k: int):
            return {"documents": docs((h.chunk_id, h.score) for h in fts.fts_search(store, query, snapshot_id, top_k))}

    @component
    class ChromaRetriever:
        @component.output_types(documents=list[Document])
        def run(self, query: str, snapshot_id: str, top_k: int):
            return {"documents": docs(dense.query(query, snapshot_id, top_k))}

    p = Pipeline()
    p.add_component("fts", Fts5Retriever())
    p.add_component("dense", ChromaRetriever())
    p.add_component("joiner", DocumentJoiner(join_mode="reciprocal_rank_fusion"))
    p.connect("fts.documents", "joiner.documents")
    p.connect("dense.documents", "joiner.documents")
    return p


class Retriever:
    def __init__(self, store: Store, llm: LLM, index_dir: Path):
        self.store = store
        self.llm = llm
        self.index_dir = Path(index_dir)
        self.dense = None
        self.pipeline = None
        try:
            self.dense = DenseIndex(self.index_dir)
            self.pipeline = _build_pipeline(store, self.dense)
        except Exception:
            log.exception("dense retrieval unavailable; FTS only")
        self.dense_ok = self.pipeline is not None

    def index_snapshot(self, snapshot_id: str) -> None:
        if not self.dense_ok:
            return
        try:
            self.dense.index_snapshot(self.store, snapshot_id)
        except Exception:  # a partial dense index would skew fusion; use FTS only from here on
            log.exception("dense indexing failed for %s; FTS only", snapshot_id)
            self.dense_ok = False

    def search(self, question: str, snapshot_id: str, k: int = 10) -> list[Hit]:
        if not fts.query_terms(question):  # only stopwords: nothing to look for
            return []
        n = max(k, CANDIDATES)
        if self.dense_ok:
            try:
                args = {"query": question, "snapshot_id": snapshot_id, "top_k": n}
                res = self.pipeline.run({"fts": args, "dense": args}, include_outputs_from={"fts", "dense"})
                # Fuse only lists that returned something: an empty dense list (snapshot not indexed) would
                # otherwise halve every score and no hit could be the RRF top.
                lists = sum(bool(res[c]["documents"]) for c in ("fts", "dense"))
                scale = 2 / lists if lists else 1.0
                scored = [(d.meta["chunk_id"], d.score * scale) for d in res["joiner"]["documents"]]
                return fts.to_hits(self.store, scored, snapshot_id, k)
            except Exception:
                log.exception("hybrid search failed; FTS only")
        # FTS only, scored as single-list RRF (Haystack's k=61, top = 1.0) so pick() treats it alike
        hits = fts.fts_search(self.store, question, snapshot_id, k)
        return [replace(h, score=RRF_K / (RRF_K + rank)) for rank, h in enumerate(hits)]

    def pick(self, question: str, hits: list[Hit]) -> list[Hit]:
        ids = pick_sections(self.llm, question, hits)
        if ids is None:  # LLM unavailable: trust the top hit only when both retrievers ranked it first
            return hits[:1] if hits and math.isclose(hits[0].score, RRF_TOP) else []
        by_id = {h.chunk_id: h for h in hits}
        return [by_id[c] for c in ids[:MAX_PICKED]]
