"""LLM prose-fact extractor (stub; Track D replaces)."""
from __future__ import annotations

from ..llm import LLM
from ..model import FactObs, PageContext


def extract(ctx: PageContext, llm: LLM) -> list[FactObs]:
    return []
