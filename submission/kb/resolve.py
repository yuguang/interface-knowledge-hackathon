"""Fact resolution (authority policy) and intent selection."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Optional

from .keys import band_contains, parse_key
from .model import FactObs, Intent, ResolvedFact
from .store import Store

_BAND_QUALIFIERS = ("tier", "loan_amount", "model_year")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_END_CUE = r"\b(?:ends?|ending|expires?|expiring|through|thru|until|valid\s+(?:through|until)|offer\s+ends\s+by)\b"


def _promo_end(quote: str) -> Optional[str]:
    """Best-effort end date (ISO) of a promo from its quote; None when none is stated. FactObs has no end-date field."""
    q = quote or ""
    m = re.search(_END_CUE + r"\s*:?\s*(\d{4})-(\d{2})-(\d{2})", q, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(_END_CUE + r"\s*:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", q, re.I)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(_END_CUE + r"\s*:?\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", q, re.I)
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def _dropped(o: FactObs) -> bool:
    """Step 5 candidates: news/press pages, and promos whose stated end date precedes the snapshot date."""
    if o.page_type == "news":
        return True
    if o.page_type == "promo":
        end = _promo_end(o.quote)
        return end is not None and end < o.taken_on
    return False


def _values(obs: list[FactObs]) -> set[str]:
    return {o.value_norm for o in obs}


def _pick(key: str, live: list[FactObs], first_seen: dict[str, str]) -> ResolvedFact:
    cands = live
    # (5) drop expired promos and news when any non-news observation exists.
    kept = [o for o in cands if not _dropped(o)]
    if kept and len(kept) < len(cands) and any(o.page_type != "news" for o in cands):
        cands = kept
    else:
        kept = cands
    # Steps 2-4 narrow the candidate set; the step at which disagreement disappears names the rule.
    stages = (
        ("authority", lambda o: o.authority),
        ("newer page date", lambda o: o.page_date or ""),
        # (4) "later snapshot of first observation": the snapshot where this observation's value first appeared
        # for the key (earliest taken_on over all snapshots up to this one); prefer the newer value.
        ("later observation", lambda o: first_seen[o.value_norm]),
    )
    collapsed = None
    for name, score in stages:
        before = cands
        best = max(score(o) for o in before)
        cands = [o for o in before if score(o) == best]
        if collapsed is None and len(_values(before)) > 1 and len(_values(cands)) == 1:
            collapsed = name
    cands = sorted(cands, key=lambda o: o.url)
    winner = cands[0]
    losers = [o for o in live if o.value_norm != winner.value_norm]
    if len(_values(cands)) > 1:
        rule = "tie: higher authority"
    elif collapsed == "authority" or (collapsed is None and losers):  # None+losers: step 5 decided
        rule = f"authority:{winner.page_type} over {max(losers, key=lambda o: o.authority).page_type}"
    else:
        rule = collapsed or "single source"
    return ResolvedFact(key, winner.value_norm, winner, losers, rule)


def resolve_snapshot(store: Store, snapshot_id: str) -> list[ResolvedFact]:
    live = store.live_urls(snapshot_id)
    by_key: dict[str, list[FactObs]] = defaultdict(list)
    for o in store.facts(snapshot_id=snapshot_id):
        if o.url in live:
            by_key[o.fact_key].append(o)
    if not by_key:
        return []
    taken_on = next(iter(next(iter(by_key.values())))).taken_on
    seen: dict[str, dict[str, str]] = defaultdict(dict)
    for o in store.facts():
        if o.fact_key in by_key and o.taken_on <= taken_on:
            cur = seen[o.fact_key].get(o.value_norm)
            if cur is None or o.taken_on < cur:
                seen[o.fact_key][o.value_norm] = o.taken_on
    return [_pick(k, by_key[k], seen[k]) for k in sorted(by_key)]


def _num(v: str) -> Optional[float]:
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", v or "")
    return float(m.group(0).replace(",", "")) if m else None


def _qual_match(name: str, want: str, have: str) -> bool:
    if name in _BAND_QUALIFIERS:
        try:
            return band_contains(have, float(str(want).replace(",", "").lstrip("$")))
        except ValueError:
            return have == want
    return have == want


def _hi(band: str) -> float:
    try:
        hi = band.split("-", 1)[1]
        return float("inf") if hi == "inf" else float(hi)
    except (IndexError, ValueError):
        return float("-inf")


def _group(fq: dict, drop: str) -> tuple:
    return tuple(sorted((k, v) for k, v in fq.items() if k != drop))


def _width(band: str) -> float:
    try:
        lo, hi = band.split("-", 1)
        return (float("inf") if hi == "inf" else float(hi)) - float(lo)
    except ValueError:
        return float("inf")


def _narrowest(sel: list, name: str) -> list:
    """Among facts differing only in band qualifier `name` (all containing the amount), keep the narrowest band."""
    best: dict[tuple, float] = {}
    for s in sel:
        if name in s[3]:
            g = _group(s[3], name)
            best[g] = min(best.get(g, float("inf")), _width(s[3][name]))
    return [s for s in sel if name not in s[3] or _width(s[3][name]) == best[_group(s[3], name)]]


def select_for_intent(intent: Intent, resolved: list[ResolvedFact]) -> list[ResolvedFact]:
    q = intent.qualifiers or {}
    parsed = [(r, parse_key(r.fact_key)) for r in resolved]
    sel = []
    for r, (p, a, fq) in parsed:
        if intent.product and p != intent.product:
            continue
        if intent.attribute and a != intent.attribute:
            continue
        if all(k in fq and _qual_match(k, v, fq[k]) for k, v in q.items()):
            sel.append((r, p, a, fq))
    for name in _BAND_QUALIFIERS:
        if name in q:
            sel = _narrowest(sel, name)
    # Headline rules for under-specified intents (qualifiers the intent did not set).
    if intent.product == "mortgage_fixed":
        if "loan_amount" not in q:
            sel = [s for s in sel if "loan_amount" not in s[3] or band_contains(s[3]["loan_amount"], 100000)]
            sel = _narrowest(sel, "loan_amount")
        if "kind" not in q:  # purchase rows (no kind) over refinance rows with the same other qualifiers
            plain = {_group(s[3], "kind") for s in sel if "kind" not in s[3]}
            sel = [s for s in sel if "kind" not in s[3] or _group(s[3], "kind") not in plain]
        if "term" not in q:
            sel = [s for s in sel if "term" not in s[3] or s[3]["term"] in ("360", "180")]
    # certificate_special without kind: no restriction; regular (no kind) and jumbo facts are both kept.
    if intent.product == "auto_loan" and "model_year" not in q:
        newest: dict[tuple, float] = {}
        for _, _, _, fq in sel:
            if "model_year" in fq:
                g = tuple(sorted((k, v) for k, v in fq.items() if k != "model_year"))
                newest[g] = max(newest.get(g, float("-inf")), _hi(fq["model_year"]))
        sel = [s for s in sel if "model_year" not in s[3] or _hi(s[3]["model_year"]) == newest[
            tuple(sorted((k, v) for k, v in s[3].items() if k != "model_year"))]]
    out = sorted((s[0] for s in sel), key=lambda r: r.fact_key)
    if intent.superlative in ("highest", "lowest"):
        nums = [(n, r) for r in out if (n := _num(r.value)) is not None]
        if nums:
            target = (max if intent.superlative == "highest" else min)(n for n, _ in nums)
            out = [r for n, r in nums if n == target]
    if intent.family != "list":
        out = out[:4]
    return out


def stale_values(store: Store, selected: list[ResolvedFact]) -> set[str]:
    keep = {r.value for r in selected}
    seen: set[str] = set()
    for key in {r.fact_key for r in selected}:
        seen |= {o.value_norm for o in store.facts(fact_key=key)}
    return seen - keep
