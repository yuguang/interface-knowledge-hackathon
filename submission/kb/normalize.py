"""Boilerplate removal, cleaning and chunking (stub; Track A replaces)."""
from __future__ import annotations

import re

from .model import Chunk, chunk_hash


def fit_boilerplate(pages: list) -> frozenset[str]:
    return frozenset()


def clean(markdown: str, boilerplate: frozenset[str]) -> str:
    return markdown


def chunk_page(url: str, title: str, cleaned: str) -> list[Chunk]:
    out = []
    for para in re.split(r"\n\s*\n", cleaned):
        text = para.strip()
        if text:
            out.append(Chunk(chunk_hash(url, text), url, title, "", text))
    return out
