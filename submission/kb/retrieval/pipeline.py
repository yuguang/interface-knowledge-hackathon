"""Retriever (stub; Track G replaces)."""
from __future__ import annotations

import re
from pathlib import Path

from ..llm import LLM
from ..model import Hit
from ..store import Store


class Retriever:
    def __init__(self, store: Store, llm: LLM, index_dir: Path):
        self.store = store
        self.llm = llm
        self.index_dir = index_dir

    def index_snapshot(self, snapshot_id: str) -> None:
        return None

    def search(self, question: str, snapshot_id: str, k: int = 10) -> list[Hit]:
        words = dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) >= 3)
        if not words:
            return []
        expr = " OR ".join(f'"{w}"' for w in words)
        hits = []
        for chunk_id, score in self.store.fts_match(expr, snapshot_id, k):
            c = self.store.chunk(chunk_id).chunk
            hits.append(Hit(chunk_id, c.url, c.heading_path, c.text, score, snapshot_id))
        return hits

    def pick(self, question: str, hits: list[Hit]) -> list[Hit]:
        return hits[:1]
