"""LLM section picker: which retrieved sections state the asked value."""
from __future__ import annotations

import re

from ..llm import HAIKU
from ..model import Hit
from .fts import query_terms

MAX_CHARS = 600
SCHEMA = {
    "type": "object",
    "properties": {
        "chunk_ids": {"type": "array", "items": {"type": "integer"}},
        "none_relevant": {"type": "boolean"},
    },
    "required": ["chunk_ids", "none_relevant"],
}
SYSTEM = (
    "You select website sections that answer a question about a credit union. "
    "Pick only sections that state the asked product's value (the rate, fee, term, amount, hours or other fact "
    "the question asks for) for that same product. A section about a different product, or one that mentions "
    "the product without stating the asked value, does not count. When the question asks for a link, URL, web "
    "address or where to go online, a section of the asked page counts: its page URL is the value. "
    "A section that only explains how such a rate works (caps, adjustments, what affects it) without stating "
    "the rate itself does not count. "
    "Return the ids of the matching sections, "
    "best first, in chunk_ids. If no section states the asked value, return an empty chunk_ids and "
    "none_relevant true."
)


def _stem(word: str) -> str:
    return word[:max(4, len(word) - 2)]


def _excerpt(question: str, text: str, limit: int = MAX_CHARS) -> str:
    """The section as the picker sees it: whole if short, else its first line plus the lines sharing the most
    question words (in page order), so a value deep in a long section is not cut off."""
    if len(text) <= limit:
        return text
    stems = {_stem(t) for t in query_terms(question)}
    lines = [ln for ln in text.split("\n") if ln.strip()]

    def score(ln: str) -> int:
        words = {_stem(w) for w in re.findall(r"[a-z0-9]+", ln.lower())}
        return len(stems & words)
    ranked = sorted(range(1, len(lines)), key=lambda i: (-score(lines[i]), i))
    if not lines or not ranked or score(lines[ranked[0]]) == 0:
        return text[:limit]
    keep, used = {0}, len(lines[0][:limit // 4])
    for i in ranked:
        if score(lines[i]) == 0 or used + len(lines[i]) + 1 > limit:
            continue
        keep.add(i)
        used += len(lines[i]) + 1
    if len(keep) == 1:  # the best line alone is longer than the budget
        keep.add(ranked[0])
    return "\n".join(lines[0][:limit // 4] if i == 0 else lines[i][:limit] for i in sorted(keep))


def _prompt(question: str, hits: list[Hit]) -> str:
    parts = [f"Question: {question}", "", "Sections:"]
    for h in hits:
        parts.append(f"[id={h.chunk_id}] {h.url}" + (f" | {h.heading_path}" if h.heading_path else ""))
        parts.append(_excerpt(question, h.text))
        parts.append("")
    return "\n".join(parts)


def pick_sections(llm, question: str, hits: list[Hit]) -> list[int] | None:
    """Chosen chunk ids (only ids among `hits`, in the model's order), [] for none, None if the LLM failed."""
    if not hits:
        return []
    out = llm.json(HAIKU, SYSTEM, _prompt(question, hits), SCHEMA, max_tokens=1024)
    if not isinstance(out, dict) or not isinstance(out.get("chunk_ids"), list):
        return None
    if out.get("none_relevant") is True:
        return []
    known = {h.chunk_id for h in hits}
    return list(dict.fromkeys(c for c in out["chunk_ids"] if type(c) is int and c in known))
