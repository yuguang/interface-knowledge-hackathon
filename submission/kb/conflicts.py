"""Conflict builder: one Conflict per fact whose value differs across snapshots or within one.

Precision filters (Task 3.3): values seen together on one page are table rows, not a disagreement;
list-valued attributes never conflict; prose values are compared by the figures they state; keys that
report one fact over time (yearly keys, relabelled model-year buckets) merge; and one change shown as
APY and rate, or under mirrored labels, is reported once.
"""
from __future__ import annotations

import re

from contract import Conflict, Source

from .keys import make_key, parse_key
from .model import FactObs
from .snapshots import SnapshotRegistry
from .store import Store

_PRODUCT_WORDS = {
    "auto_loan": "auto loan", "mortgage_fixed": "fixed mortgage", "mortgage_arm": "ARM mortgage",
    "heloc": "HELOC", "home_equity_loan": "home equity loan", "personal_loan": "personal loan",
    "student_loan": "student loan", "construction_loan": "construction loan", "rv_loan": "RV loan",
    "certificate": "certificate", "certificate_special": "certificate special",
    "share_savings": "share savings", "money_market": "money market", "checking": "checking",
    "bright_savings": "Bright Savings", "jump_start": "Jump Start", "ira": "IRA",
    "credit_card": "credit card", "branch": "branch", "org": "organization", "program": "program",
}
_ATTR_WORDS = {
    "apr": "APR", "apy": "APY", "rate": "rate", "min_balance": "minimum balance",
    "hours_lobby": "lobby hours", "hours_driveup": "drive-up hours", "address": "address", "phone": "phone",
    "status": "status", "closure_date": "closure date", "routing_number": "routing number",
    "count": "count", "url": "URL", "award": "award", "eligibility": "eligibility",
}
_YEAR_PRODUCTS = {"mortgage_fixed", "mortgage_arm", "heloc", "home_equity_loan"}


def _money(n: str) -> str:
    try:
        return f"${int(float(n)):,}"
    except ValueError:
        return n


def _band_words(band: str) -> str:
    lo, sep, hi = band.partition("-")
    if not sep:
        return band
    if hi == "inf":
        return f"{_money(lo)} and up"
    return f"{_money(lo)}-{_money(hi)}"


def _term_words(product: str, term: str) -> str:
    if not term.isdigit():
        return term
    m = int(term)
    if product in _YEAR_PRODUCTS and m >= 12 and m % 12 == 0:
        return f"{m // 12}-year"
    return f"{m}-month"


def _topic(key: str) -> str:
    product, attr, q = parse_key(key)
    words = []
    if "term" in q:
        words.append(_term_words(product, q["term"]))
    if "segment" in q:
        words.append(q["segment"])
    words.append(_PRODUCT_WORDS.get(product, product.replace("_", " ")))
    words.append(_ATTR_WORDS.get(attr, attr.replace("_", " ")))
    if "model_year" in q:
        a, _, b = q["model_year"].partition("-")
        words.append(f"{a}-{b} model year" if b and b != a else f"{a} model year")
    for name in ("tier", "loan_amount"):
        if name in q:
            words.append(_band_words(q[name]))
    for name in ("kind", "name", "what", "day"):
        if name in q:
            words.append(q[name])
    return " ".join(words)


def _best(obs: list[FactObs]) -> FactObs:
    return max(obs, key=lambda o: (o.authority, o.taken_on, o.url))


# Qualifiers that pick a row of a rate table (or a point in time). Two keys that differ only in the
# other qualifiers (product label, segment, kind, name) but share these are candidates for being the
# same published row shown under several labels.
_ROW_QUALS = ("term", "tier", "loan_amount", "model_year", "day", "what", "year")
# List-valued attributes: several distinct values are several items, not a disagreement.
_LIST_ATTRS = {"detail", "eligibility"}
# Prose-valued attributes: two values disagree only when the figures they state differ.
_PROSE_ATTRS = {"award", "count"}
# A headline metric beats the derived one when the same row conflicts under both.
_METRIC_SHADOW = {"rate": "apy", "apr": "rate"}  # drop key attr when the mapped attr also conflicts

_NUM_WORDS = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
              "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred "
              "first second third fourth fifth sixth seventh eighth ninth tenth").split()
_CLAIM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*[kKmM]?\b|\b(?:" + "|".join(_NUM_WORDS) + r")\b", re.I)


def _claim(value: str) -> frozenset[str]:
    """The figures a prose value states ("twenty renewable scholarships" -> {"twenty"}), for comparison."""
    out = set()
    for tok in _CLAIM_RE.findall(value or ""):
        t = tok.lower().replace("$", "").replace(",", "").replace(" ", "")
        out.add(t)
    return frozenset(out)


def _trusted(by_value: dict[str, list[FactObs]]) -> dict[str, list[FactObs]]:
    """Drop values that rest on a single LLM observation: such disagreement is not trusted."""
    return {v: obs for v, obs in by_value.items()
            if not (len(obs) == 1 and obs[0].extractor.startswith("llm:"))}


def _disagree(attr: str, a: str, b: str) -> bool:
    if a == b:
        return False
    if attr in _PROSE_ATTRS:
        ca, cb = _claim(a), _claim(b)
        return bool(ca) and bool(cb) and not (ca & cb)
    return True


def _disputed_values(attr: str, by_value: dict[str, list[FactObs]]) -> list[str]:
    """Values that take part in at least one real disagreement, in first-seen order.

    Two values shown on the same page in the same snapshot are two rows whose distinguishing
    qualifier we did not read (a tiered table), not a contradiction, so such a pair never counts.
    """
    pages = {v: {(o.snapshot_id, o.url) for o in obs} for v, obs in by_value.items()}
    vals = list(by_value)
    hit: set[str] = set()
    for i, a in enumerate(vals):
        for b in vals[i + 1:]:
            if _disagree(attr, a, b) and not (pages[a] & pages[b]):
                hit.update((a, b))
    return [v for v in vals if v in hit]


def _strip(key: str, drop: str) -> tuple[str, str, tuple]:
    product, attr, q = parse_key(key)
    return product, attr, tuple(sorted((k, v) for k, v in q.items() if k != drop))


def _year_span(r: str):
    lo, sep, hi = r.partition("-")
    try:
        return (int(lo), int(hi or lo))
    except ValueError:
        return None


def _span_jaccard(a, b) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0]) + 1
    if inter <= 0:
        return 0.0
    return inter / (max(a[1], b[1]) - min(a[0], b[0]) + 1)


def _key_groups(obs_by_key: dict[str, list[FactObs]]) -> list[list[str]]:
    """Fact keys that report one fact over time, merged into one conflict candidate.

    - Keys that differ only in `year`: a yearly figure (a ranking, a count) is one fact whose
      value moves from year to year.
    - Keys that differ only in `model_year` whose ranges overlap and that never appear on the same
      page in the same snapshot: the site relabelled its model-year buckets ("2023-26" became
      "2024-27"), so the row is the same. Each key pairs with its best-overlapping partner only,
      and only when the choice is mutual.
    """
    parent = {k: k for k in obs_by_key}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        parent[find(a)] = find(b)

    by_sans_year: dict[tuple, list[str]] = {}
    by_sans_my: dict[tuple, list[str]] = {}
    for k in obs_by_key:
        q = parse_key(k)[2]
        if "year" in q:
            by_sans_year.setdefault(_strip(k, "year"), []).append(k)
        if "model_year" in q and _year_span(q["model_year"]):
            by_sans_my.setdefault(_strip(k, "model_year"), []).append(k)
    for ks in by_sans_year.values():
        for k in ks[1:]:
            union(ks[0], k)
    for ks in by_sans_my.values():
        if len(ks) < 2:
            continue
        span = {k: _year_span(parse_key(k)[2]["model_year"]) for k in ks}
        pages = {k: {(o.snapshot_id, o.url) for o in obs_by_key[k]} for k in ks}
        best = {}
        for a in ks:
            scored = [(_span_jaccard(span[a], span[b]), b) for b in ks if b != a and not (pages[a] & pages[b])]
            scored = [s for s in scored if s[0] > 0]
            if scored:
                best[a] = max(scored)[1]
        for a, b in best.items():
            if best.get(b) == a:
                union(a, b)
    groups: dict[str, list[str]] = {}
    for k in obs_by_key:
        groups.setdefault(find(k), []).append(k)
    return [sorted(g) for g in groups.values()]


class _Resolver:
    """Latest resolved fact per key (snapshot 05, or the last snapshot the key was resolved in)."""

    def __init__(self, store: Store, registry: SnapshotRegistry):
        self._store, self._ids = store, registry.ids()
        self._cache: dict[tuple, dict[str, object]] = {}

    def __call__(self, key: str):
        product, attr, _ = parse_key(key)
        for sid in reversed(self._ids):
            ck = (sid, product, attr)
            if ck not in self._cache:
                self._cache[ck] = {r.fact_key: r for r in self._store.resolved(sid, product, attr)}
            r = self._cache[ck].get(key)
            if r is not None:
                return sid, r
        return None, None


def _pick_resolution(resolver: _Resolver, ids: list[str], members: list[str]):
    """(key, sid, resolved fact) whose value is current: resolved in the latest snapshot first, then the
    newest `year`, then the highest-authority winner."""
    best = None
    for k in members:
        sid, rf = resolver(k)
        if rf is None:
            continue
        rank = (ids.index(sid), parse_key(k)[2].get("year", ""), rf.winner.authority, k)
        if best is None or rank > best[0]:
            best = (rank, k, sid, rf)
    return (best[1], best[2], best[3]) if best else (None, None, None)


def _preference(key: str) -> tuple:
    """Which of several mirrored keys names the conflict: the plainest label."""
    product, _, q = parse_key(key)
    return (len(q), product == "ira", q.get("segment", "personal") != "personal", key)


def _dedupe(cands: list[tuple[str, tuple, Conflict]]) -> list[tuple[str, tuple, Conflict]]:
    """One conflict per underlying change.

    - Metric shadow: the same row conflicting as APY and dividend rate (or rate and APR) is one change;
      keep the headline metric.
    - Mirrors: the same row (equal row qualifiers, same current value) under several labels
      (business/personal, IRA/certificate, purchase/refinance) whose value histories nest is one
      change; keep the plainest label.
    """
    present = {(parse_key(k)[0], parse_key(k)[1], _strip(k, "")[2]) for k, _, _ in cands}
    kept = []
    for k, m, c in cands:
        product, attr, q = parse_key(k)
        shadow = _METRIC_SHADOW.get(attr)
        if shadow and (product, shadow, _strip(k, "")[2]) in present:
            continue
        kept.append((k, m, c))
    rows: dict[tuple, list[tuple[str, tuple, Conflict]]] = {}
    for k, m, c in kept:
        _, attr, q = parse_key(k)
        row = tuple((n, q[n]) for n in _ROW_QUALS if n in q)
        rows.setdefault((attr, row, c.resolution), []).append((k, m, c))
    out = []
    for group in rows.values():
        chosen: list[tuple[str, tuple, Conflict]] = []
        for k, m, c in sorted(group, key=lambda kc: _preference(kc[0])):
            vs = set(c.values)
            if any(vs <= set(x.values) or vs >= set(x.values) for _, _, x in chosen):
                continue
            chosen.append((k, m, c))
        out.extend(chosen)
    return out


def build_conflicts(store: Store, registry: SnapshotRegistry) -> list[Conflict]:
    return [c for _, _, c in build_conflicts_detailed(store, registry)]


def build_conflicts_detailed(store: Store, registry: SnapshotRegistry) -> list[tuple[str, tuple, Conflict]]:
    """(fact key naming the conflict, the fact keys merged into it, Conflict), sorted by topic then values."""
    ids = registry.ids()
    if not ids:
        return []
    latest = registry.latest()
    resolver = _Resolver(store, registry)
    obs_by_key: dict[str, list[FactObs]] = {}
    for o in store.facts():
        if o.attribute not in _LIST_ATTRS:
            obs_by_key.setdefault(o.fact_key, []).append(o)
    cands: list[tuple[str, tuple, Conflict]] = []
    for members in _key_groups(obs_by_key):
        attr = parse_key(members[0])[1]
        yearly = len(members) > 1 and all("year" in parse_key(k)[2] for k in members)
        obs = sorted((o for k in members for o in obs_by_key[k]),
                     key=lambda o: (o.qualifiers.get("year", "") if yearly else "", o.taken_on, o.url, o.fact_key))
        by_value: dict[str, list[FactObs]] = {}
        for o in obs:  # ordered by (year,) taken_on: dict keeps first-seen order
            by_value.setdefault(o.value_norm, []).append(o)
        by_value = _trusted(by_value)
        values = _disputed_values(attr, by_value)
        if len(values) < 2:
            continue
        rkey, rsid, rf = _pick_resolution(resolver, ids, members)
        if rf is not None:
            resolution = rf.value
        else:
            rkey, resolution = obs[-1].fact_key, obs[-1].value_norm
        if resolution in by_value and resolution not in values:
            values.append(resolution)
        reason = None
        if rf is not None and rsid == latest and any(l.value_norm != rf.value for l in rf.losers):
            reason = rf.rule
        if reason is None and yearly:
            reason = f"most recent year ({parse_key(rkey)[2]['year']})"
        if reason is None:
            # first snapshot of the winner's final unbroken run
            run_start = None
            for sid in ids:
                vals = {o.value_norm for o in obs if o.snapshot_id == sid}
                if not vals:
                    continue
                if resolution in vals:
                    run_start = run_start or sid
                else:
                    run_start = None
            reason = f"updated in {run_start}" if run_start else f"latest value {resolution}"
        sources = []
        for v in values:
            b = _best(by_value[v])
            sources.append(Source(url=b.url, snapshot_id=b.snapshot_id, quote=b.quote))
        # a merged group is named by its current key; a year-merged one drops the year
        name = rkey if len(members) > 1 else members[0]
        if yearly:
            p, a, q = parse_key(name)
            name = make_key(p, a, {n: v for n, v in q.items() if n != "year"})
        cands.append((name, tuple(members), Conflict(topic=_topic(name), values=values, sources=sources,
                                     resolution=resolution, reason=reason)))
    out = _dedupe(cands)
    out.sort(key=lambda kc: (kc[2].topic, kc[2].values))
    return out
