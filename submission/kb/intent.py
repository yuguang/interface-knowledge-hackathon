"""Intent parser (stub; Track H replaces)."""
from __future__ import annotations

from typing import Optional

from .llm import LLM
from .model import Intent


def parse(question: str, llm: Optional[LLM] = None) -> Intent:
    return Intent("prose", None, None, {}, None, question, question.lower().strip())
