"""LLM prose-fact extractor (Track D): Haiku reads in-scope chunks and emits structured facts.

Flow per page: for each in-scope chunk, look the chunk hash up in the committed cache
(`submission/data/facts_cache.jsonl`, read once, lazily); on a miss ask Haiku (unless the per-snapshot
LLM time budget is spent). The cache holds derived values only (no quote, no page text): `quote` is
re-derived from the chunk text, and a fact whose value does not occur in the chunk text is dropped.

Time budget: cumulative wall time spent inside `LLM.json` calls is accumulated per snapshot_id in the
module-level `_spent`; once it exceeds BUDGET_S, remaining cache misses in that snapshot are skipped
(cache hits still work).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from .. import keys
from ..llm import HAIKU, LLM
from ..model import FactObs, PageContext, StoredChunk

PROMPT_VER = "v1"
EXTRACTOR = f"llm:{PROMPT_VER}"
SCOPE_TYPES = frozenset({"product", "branch", "other", "promo"})
SCOPE_RE = re.compile(r"eligib|Home\$tart|student loan|VTO|award|mobile wallet|Apple Pay|Google Pay|Samsung Pay",
                      re.I)
BUDGET_S = 300.0
CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "facts_cache.jsonl"

_spent: dict[str, float] = {}  # snapshot_id -> cumulative seconds inside LLM.json
_cache: Optional[dict[str, list[dict]]] = None

SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "product": {"type": "string", "enum": list(keys.PRODUCTS)},
            "attribute": {"type": "string", "enum": list(keys.ATTRIBUTES)},
            "qualifiers": {"type": "object",
                           "properties": {q: {"type": "string"} for q in keys.QUALIFIERS}},
            "value": {"type": "string"},
        },
        "required": ["product", "attribute", "qualifiers", "value"],
    }}},
    "required": ["facts"],
}

SYSTEM = (
    "You extract facts about a credit union's products from one passage of its website. "
    "Return only facts explicitly stated in the passage, with `value` copied verbatim from the passage. "
    "Use only the allowed product, attribute and qualifier names. Examples of useful facts: eligibility "
    "rules, awards, mobile wallet support (Apple Pay, Google Pay, Samsung Pay), program names, student "
    "loan terms, Home$tart details. If the passage states no such fact, return {\"facts\": []}."
)


def in_scope(page_type: str, text: str) -> bool:
    return page_type in SCOPE_TYPES and bool(SCOPE_RE.search(text))


def _load_cache() -> dict[str, list[dict]]:
    global _cache
    if _cache is None:
        c: dict[str, list[dict]] = {}
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if row.get("prompt_ver") == PROMPT_VER:
                            c[row["chunk_hash"]] = row["facts"]
                    except (ValueError, KeyError, AttributeError):
                        continue
        except OSError:
            pass
        _cache = c
    return _cache


def _norm_value(attribute: str, value: str) -> Optional[str]:
    if attribute in ("apr", "apy", "rate"):
        return keys.norm_percent(value)
    if attribute == "min_balance":
        return keys.norm_money(value)
    return " ".join(value.split())


def _squash(s: str) -> str:
    return " ".join(s.split())


def _quote(text: str, value: str) -> Optional[str]:
    """Sentence of `text` containing `value` (<= 200 chars); None if the value is not in the text."""
    flat = _squash(text)
    v = _squash(value)
    i = flat.lower().find(v.lower()) if v else -1
    if i < 0:
        return None
    start = max(flat.rfind(". ", 0, i), flat.rfind("! ", 0, i), flat.rfind("? ", 0, i), flat.rfind("\n", 0, i))
    start = 0 if start < 0 else start + 2
    ends = [e for e in (flat.find(". ", i + len(v)), flat.find("! ", i + len(v)), flat.find("? ", i + len(v)))
            if e >= 0]
    end = min(ends) + 1 if ends else len(flat)
    sent = flat[start:end].strip()
    if len(sent) > 200:
        lo = max(0, min(i - start - 80, len(sent) - 200))
        sent = sent[lo:lo + 200]
    return sent


def _to_obs(ctx: PageContext, sc: StoredChunk, raw: dict) -> Optional[FactObs]:
    try:
        product, attribute = raw["product"], raw["attribute"]
        quals = {str(k): str(v) for k, v in (raw.get("qualifiers") or {}).items() if str(v).strip()}
        value = str(raw["value"]).strip()
        key = keys.make_key(product, attribute, quals)
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if not value:
        return None
    quote = _quote(sc.chunk.text, value)
    norm = _norm_value(attribute, value)
    if quote is None or norm is None:
        return None
    info = ctx.info
    return FactObs(key, product, attribute, quals, value, norm, ctx.snapshot_id, ctx.taken_on, ctx.url,
                   info.page_type, info.authority, info.page_date, quote, EXTRACTOR, sc.chunk_id)


def ask(llm: LLM, text: str, title: str = "", heading: str = "") -> Optional[list[dict]]:
    """One Haiku call for one chunk; the validated raw facts, or None on failure."""
    user = f"Page: {title}\nSection: {heading}\n\nPassage:\n{text}"
    out = llm.json(HAIKU, SYSTEM, user, SCHEMA, max_tokens=2048, timeout_s=30.0)
    if not isinstance(out, dict) or not isinstance(out.get("facts"), list):
        return None
    good = []
    for f in out["facts"]:
        try:
            keys.make_key(f["product"], f["attribute"], f.get("qualifiers") or {})
            good.append({"product": f["product"], "attribute": f["attribute"],
                         "qualifiers": dict(f.get("qualifiers") or {}), "value": str(f["value"])})
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return good


def extract(ctx: PageContext, llm: LLM) -> list[FactObs]:
    cache = _load_cache()
    out: list[FactObs] = []
    for sc in ctx.chunks:
        ch = sc.chunk
        if not in_scope(ctx.info.page_type, ch.text):
            continue
        raw = cache.get(ch.hash)
        if raw is None:
            if _spent.get(ctx.snapshot_id, 0.0) > BUDGET_S:
                continue
            t0 = time.monotonic()
            try:
                raw = ask(llm, ch.text, ch.title, ch.heading_path)
            finally:
                _spent[ctx.snapshot_id] = _spent.get(ctx.snapshot_id, 0.0) + time.monotonic() - t0
            if raw is None:
                continue
        for r in raw:
            obs = _to_obs(ctx, sc, r)
            if obs is not None:
                out.append(obs)
    return out
