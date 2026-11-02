"""Fact resolution (authority policy) and intent selection."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Optional

from . import tracing
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


_STREET = {"avenue": "ave", "street": "st", "road": "rd", "boulevard": "blvd", "drive": "dr", "highway": "hwy",
           "lane": "ln", "parkway": "pkwy", "court": "ct", "place": "pl", "circle": "cir"}


def _name_tokens(slug: str) -> set[str]:
    return {_STREET.get(t, t) for t in str(slug).split("-") if t}


def _qual_match(name: str, want: str, have: str) -> bool:
    if name in _BAND_QUALIFIERS:
        try:
            return band_contains(have, float(str(want).replace(",", "").lstrip("$")))
        except ValueError:
            return have == want
    if name == "name":
        # Tracks slug names differently ("madison-mineral-point-road" vs "mineral-point-road"): the asked name's
        # tokens must all occur in the fact's name (street words canonicalised: "avenue" == "ave").
        w = _name_tokens(want)
        return bool(w) and w <= _name_tokens(have)
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


_DEPOSITS = frozenset({"certificate", "certificate_special", "share_savings", "money_market", "checking",
                       "bright_savings", "jump_start", "ira"})
# Extra non-band qualifier one key may carry over another and still be the same account (LLM facts without
# `segment`). `name` is not one: Achieve Money Market and the plain money market share tier bands but are different
# accounts; checking's name split (1st Rate on /rates, unnamed on landing pages) is removed at the source (C1).
_SUBSET_EXTRA = frozenset({"segment"})


def rate_attributes(product: Optional[str]) -> tuple[str, ...]:
    """Attributes a generic "rate" question covers: deposits are quoted as APY, loans and cards as APR."""
    return ("apy", "rate") if product in _DEPOSITS else ("apr", "rate")


def attribute_matches(intent: Intent, attribute: str) -> bool:
    """An intent with a product but no attribute is a generic "rate" question (intent.parse): rate plus APY/APR."""
    if intent.attribute:
        return attribute == intent.attribute
    return not intent.product or attribute in rate_attributes(intent.product)


def matching(intent: Intent, resolved: list[ResolvedFact]) -> list[ResolvedFact]:
    """Resolved facts of the intent's product and attribute(s), qualifiers ignored."""
    out = []
    for r in resolved:
        p, a, _ = parse_key(r.fact_key)
        if (not intent.product or p == intent.product) and attribute_matches(intent, a):
            out.append(r)
    return out


def _bounds(band: str) -> tuple[float, float]:
    lo, hi = band.split("-", 1)
    return float(lo), (float("inf") if hi == "inf" else float(hi))


def _band_compatible(a: str, b: str, asked: Optional[str]) -> bool:
    """Bands that can describe the same rate. Asked amount/year: both contain it (checked by the caller).
    Nothing asked: (nearly) the same band ("0-4999.99" vs "0.01-4999.99"), or a partial overlap where neither
    band contains the other ("2023-2026" vs "2024-2027"). Nested bands are a tier structure ("$100,000 or less"
    inside "$200,000 or less", "$1,000+" around "$100,000+") and stay distinct."""
    if a == b or asked is not None:
        return True
    try:
        (la, ha), (lb, hb) = _bounds(a), _bounds(b)
    except ValueError:
        return False
    if ha == hb and abs(la - lb) < 1:
        return True
    overlap = max(la, lb) <= min(ha, hb)
    nested = (la <= lb and hb <= ha) or (lb <= la and ha <= hb)
    return overlap and not nested


def _same_account(fa: dict, fb: dict, q: dict) -> bool:
    """Two keys of one product+attribute that describe the same account/rate (so only one may answer)."""
    ba = {k: v for k, v in fa.items() if k in _BAND_QUALIFIERS}
    bb = {k: v for k, v in fb.items() if k in _BAND_QUALIFIERS}
    if set(ba) != set(bb) or not all(_band_compatible(ba[k], bb[k], q.get(k)) for k in ba):
        return False
    na = {k: v for k, v in fa.items() if k not in _BAND_QUALIFIERS}
    nb = {k: v for k, v in fb.items() if k not in _BAND_QUALIFIERS}
    if na == nb:
        return True
    small, big = (na, nb) if len(na) <= len(nb) else (nb, na)
    return all(big.get(k) == v for k, v in small.items()) and set(big) - set(small) <= _SUBSET_EXTRA


def _band_width(fq: dict) -> float:
    return sum(_width(fq[k]) for k in _BAND_QUALIFIERS if k in fq)


def _collapse_same_account(sel: list, q: dict) -> list:
    """Keep one candidate per account split across keys (overlapping bands, or one key's qualifiers a subset of
    the other's): best first by winner authority, then newest page date, then narrowest band, then smallest key;
    a candidate is dropped when it is the same account as one already kept (greedy, so overlaps don't chain)."""
    ranked = sorted(sel, key=lambda s: s[0].fact_key)
    ranked = sorted(ranked, key=lambda s: (s[0].winner.authority, s[0].winner.page_date or "", -_band_width(s[3])),
                    reverse=True)
    kept: list = []
    for s in ranked:
        if not any(k[1:3] == s[1:3] and _same_account(k[3], s[3], q) for k in kept):
            kept.append(s)
    return kept


def _num_or_text(v: str) -> tuple:
    try:
        return (0, float(v), "")
    except ValueError:
        return (1, 0.0, v)


def _order(s: tuple) -> tuple:
    """Numeric qualifier order (bands by low then high bound, terms by months); APY/APR before rate."""
    _, p, a, fq = s
    quals = []
    for k in sorted(fq):
        v = fq[k]
        if k in _BAND_QUALIFIERS and "-" in v:
            lo, hi = v.split("-", 1)
            quals.append((k, _num_or_text(lo), _num_or_text(hi)))
        else:
            quals.append((k, _num_or_text(v), (0, 0.0, "")))
    return p, tuple(quals), 0 if a in ("apy", "apr") else 1, a


@tracing.traced("kb.select_facts", "tool")
def select_for_intent(intent: Intent, resolved: list[ResolvedFact]) -> list[ResolvedFact]:
    q = intent.qualifiers or {}
    parsed = [(r, parse_key(r.fact_key)) for r in resolved]
    sel = []
    for r, (p, a, fq) in parsed:
        if intent.product and p != intent.product:
            continue
        if not attribute_matches(intent, a):
            continue
        if all(k in fq and _qual_match(k, v, fq[k]) for k, v in q.items()):
            sel.append((r, p, a, fq))
    sel = _collapse_same_account(sel, q)
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
    if "year" not in q:  # year-keyed facts (Forbes awards, R33): an undated question means the latest year
        def _yr(fq: dict) -> Optional[str]:  # LLM facts sometimes misuse `year` for a band: only real years count
            y = fq.get("year")
            return y if y and re.fullmatch(r"(?:19|20)\d\d", y) else None
        latest: dict[tuple, str] = {}
        for s in sel:
            if _yr(s[3]):
                g = (s[1], s[2], _group(s[3], "year"))
                latest[g] = max(latest.get(g, ""), _yr(s[3]))
        sel = [s for s in sel if not _yr(s[3]) or _yr(s[3]) == latest[(s[1], s[2], _group(s[3], "year"))]]
    sel = sorted(sel, key=_order)
    if intent.superlative in ("highest", "lowest"):
        nums = [(n, s) for s in sel if (n := _num(s[0].value)) is not None]
        if nums:
            target = (max if intent.superlative == "highest" else min)(n for n, _ in nums)
            sel = [s for n, s in nums if n == target]
    if intent.family != "list":  # at most 4 accounts; a generic "rate" keeps each account's APY/APR and rate
        accounts: list[tuple] = []
        for s in sel:
            acct = (s[1], _group(s[3], ""))
            if acct not in accounts:
                accounts.append(acct)
        sel = [s for s in sel if (s[1], _group(s[3], "")) in accounts[:4]]
    return [s[0] for s in sel]


@tracing.traced("kb.stale_values", "tool", tags=["guardrail"])
def stale_values(store: Store, selected: list[ResolvedFact]) -> set[str]:
    keep = {r.value for r in selected}
    seen: set[str] = set()
    for key in {r.fact_key for r in selected}:
        seen |= {o.value_norm for o in store.facts(fact_key=key)}
    return seen - keep
