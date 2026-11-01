"""Keyword retrieval: SQLite FTS5 (bm25), with a rank_bm25 fallback when FTS5 is missing."""
from __future__ import annotations

import re
import weakref

from ..model import Hit
from ..store import Store

STOPWORDS = frozenset("""
a about am an and any are as at be been but by can could did do does for from get got had has have how i if in
is it its me my of on or our please s should so tell than that the their them then there these they this to us
was we were what whats when where which who why will with would you your
""".split())

# Each group: any member in the question adds all members to the query.
SYNONYMS = (("cd", "certificate"), ("apr", "rate"))

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def query_terms(question: str) -> list[str]:
    """Question words minus stopwords and single characters, plus synonyms; deduplicated, in order."""
    terms = [t for t in _tokens(question.replace("'", "")) if len(t) >= 2 and t not in STOPWORDS]
    for group in SYNONYMS:
        if any(t in group for t in terms):
            terms += group
    return list(dict.fromkeys(terms))


def to_match_expr(question: str) -> str:
    """FTS5 MATCH expression: quoted terms OR-joined. Tokens are [a-z0-9]+, so no FTS syntax survives."""
    return " OR ".join(f'"{t}"' for t in query_terms(question))


def to_hits(store: Store, scored: list[tuple[int, float]], snapshot_id: str, k: int) -> list[Hit]:
    """Hits sorted score desc, then url asc, then chunk_id asc; top k."""
    hits = []
    for cid, score in scored:
        c = store.chunk(cid).chunk
        hits.append(Hit(cid, c.url, c.heading_path, c.text, float(score), snapshot_id))
    hits.sort(key=lambda h: (-h.score, h.url, h.chunk_id))
    return hits[:k]


class Bm25Fallback:
    """rank_bm25 over one snapshot's chunks, for SQLite builds without FTS5."""

    def __init__(self, store: Store, snapshot_id: str):
        from rank_bm25 import BM25Okapi
        self.ids = sorted(store.chunk_ids_in(snapshot_id))
        corpus = [_tokens(store.chunk(cid).chunk.text) or ["_"] for cid in self.ids]
        self.bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, question: str, k: int) -> list[tuple[int, float]]:
        terms = query_terms(question)
        if not terms or self.bm25 is None:
            return []
        scores = self.bm25.get_scores(terms)
        ranked = sorted(((float(s), cid) for s, cid in zip(scores, self.ids) if s > 0), key=lambda x: (-x[0], x[1]))
        return [(cid, s) for s, cid in ranked[:k]]


# store -> {snapshot_id: (n_chunks, Bm25Fallback)}; rebuilt when the snapshot's chunk count changes
_fallbacks: "weakref.WeakKeyDictionary[Store, dict]" = weakref.WeakKeyDictionary()


def _fallback(store: Store, snapshot_id: str) -> Bm25Fallback:
    per = _fallbacks.setdefault(store, {})
    n = len(store.chunk_ids_in(snapshot_id))
    if snapshot_id not in per or per[snapshot_id][0] != n:
        per[snapshot_id] = (n, Bm25Fallback(store, snapshot_id))
    return per[snapshot_id][1]


def fts_search(store: Store, question: str, snapshot_id: str, k: int) -> list[Hit]:
    if store.fts_available:
        expr = to_match_expr(question)
        scored = store.fts_match(expr, snapshot_id, k) if expr else []
    else:
        scored = _fallback(store, snapshot_id).search(question, k)
    return to_hits(store, scored, snapshot_id, k)
