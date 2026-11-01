"""LLM section picker: which retrieved sections state the asked value."""
from __future__ import annotations

from ..llm import HAIKU
from ..model import Hit

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
    "the product without stating the asked value, does not count. Return the ids of the matching sections, "
    "best first, in chunk_ids. If no section states the asked value, return an empty chunk_ids and "
    "none_relevant true."
)


def _prompt(question: str, hits: list[Hit]) -> str:
    parts = [f"Question: {question}", "", "Sections:"]
    for h in hits:
        parts.append(f"[id={h.chunk_id}] {h.url}" + (f" | {h.heading_path}" if h.heading_path else ""))
        parts.append(h.text[:MAX_CHARS])
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
