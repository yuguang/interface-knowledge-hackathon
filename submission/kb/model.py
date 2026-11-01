"""Shared immutable types used by every stage of the knowledge system."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

PAGE_TYPES = ("rate_sheet", "product", "branch", "disclosure", "other", "calculator", "promo", "blog", "news", "legacy")
AUTHORITY = {"rate_sheet": 100, "product": 80, "branch": 70, "disclosure": 60, "other": 40,
             "calculator": 30, "promo": 25, "blog": 15, "news": 10, "legacy": 5}
NUMERIC_ATTRS = {"apr", "apy", "rate", "min_balance", "count"}


@dataclass(frozen=True)
class PageInfo:
    page_type: str
    authority: int
    page_date: Optional[str]  # ISO


def chunk_hash(url: str, text: str) -> str:
    """Chunk identity: sha256(url + "\\n" + text). It covers the url, so identical text on two pages is two chunks."""
    return hashlib.sha256((url + "\n" + text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Chunk:
    """`hash` must be chunk_hash(url, text); identity is per URL (R6)."""
    hash: str
    url: str
    title: str
    heading_path: str
    text: str


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: int
    chunk: Chunk


@dataclass(frozen=True)
class PageContext:
    snapshot_id: str
    taken_on: str
    url: str
    title: str
    info: PageInfo
    markdown: str  # cleaned
    chunks: list[StoredChunk]


@dataclass(frozen=True)
class FactObs:
    fact_key: str
    product: str
    attribute: str
    qualifiers: dict[str, str]
    value_raw: str
    value_norm: str
    snapshot_id: str
    taken_on: str
    url: str
    page_type: str
    authority: int
    page_date: Optional[str]
    quote: str  # <= 200 chars
    extractor: str  # "table" | "regex" | "llm:<prompt_ver>"
    chunk_id: Optional[int] = None


@dataclass(frozen=True)
class ResolvedFact:
    fact_key: str
    value: str
    winner: FactObs
    losers: list[FactObs]
    rule: str


@dataclass(frozen=True)
class Intent:
    family: str  # "fact" | "list" | "prose"
    product: Optional[str]
    attribute: Optional[str]
    qualifiers: dict[str, str]
    superlative: Optional[str]  # "highest" | "lowest" | None
    cleaned_question: str
    canonical: str  # memo key; equal for paraphrases


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    url: str
    heading_path: str
    text: str
    score: float
    snapshot_id: str
