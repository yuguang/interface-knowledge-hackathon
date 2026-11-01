"""Answer composition (stub; Track I replaces)."""
from __future__ import annotations

from contract import Answer, Source

from .llm import LLM
from .model import Hit, Intent, ResolvedFact

_REFUSAL = "That isn't listed on the website, so I can't confirm it. Please contact Dupaco directly for this information."


def render_facts(question: str, intent: Intent, selected: list[ResolvedFact], snapshot_id: str) -> Answer:
    sources = [Source(r.winner.url, r.winner.snapshot_id, r.winner.quote) for r in selected]
    return Answer(text=", ".join(r.value for r in selected), sources=sources)


def render_prose(question: str, intent: Intent, hits: list[Hit], snapshot_id: str, llm: LLM) -> Answer:
    h = hits[0]
    return Answer(text=h.text[:600], sources=[Source(h.url, snapshot_id, h.text[:200])])


def refusal(question: str) -> Answer:
    return Answer(text=_REFUSAL, confident=False)


def sanitize(answer: Answer, keep: set[str], drop: set[str]) -> Answer:
    return answer
