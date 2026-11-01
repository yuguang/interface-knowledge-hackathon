"""Conflict builder: one Conflict per fact key whose value differs across snapshots or within one."""
from __future__ import annotations

from contract import Conflict, Source

from .keys import parse_key
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


def _is_noise(by_value: dict[str, list[FactObs]]) -> bool:
    """Disagreement that rests only on single-observation LLM extractions is not trusted."""
    solid = [v for v, obs in by_value.items() if not (len(obs) == 1 and obs[0].extractor.startswith("llm:"))]
    return len(solid) < 2


def _resolution(store: Store, registry: SnapshotRegistry, key: str):
    """(resolved fact in snapshot 05, or the last resolved fact if the key is gone there, or None)."""
    product, attr, _ = parse_key(key)
    for sid in reversed(registry.ids()):
        for r in store.resolved(sid, product, attr):
            if r.fact_key == key:
                return sid, r
    return None, None


def build_conflicts(store: Store, registry: SnapshotRegistry) -> list[Conflict]:
    ids = registry.ids()
    if not ids:
        return []
    latest = registry.latest()
    out: list[Conflict] = []
    for key in store.fact_keys():
        _, attr, _ = parse_key(key)
        if attr == "detail":
            continue
        obs = store.facts(fact_key=key)
        by_value: dict[str, list[FactObs]] = {}
        for o in obs:  # facts() is ordered by taken_on: dict keeps first-seen order
            by_value.setdefault(o.value_norm, []).append(o)
        if len(by_value) < 2 or _is_noise(by_value):
            continue
        rsid, rf = _resolution(store, registry, key)
        if rf is not None:
            resolution = rf.value
        else:
            resolution = obs[-1].value_norm
        reason = None
        if rf is not None and rsid == latest and any(l.value_norm != rf.value for l in rf.losers):
            reason = rf.rule
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
        for v, vobs in by_value.items():
            b = _best(vobs)
            sources.append(Source(url=b.url, snapshot_id=b.snapshot_id, quote=b.quote))
        out.append(Conflict(topic=_topic(key), values=list(by_value), sources=sources,
                            resolution=resolution, reason=reason))
    out.sort(key=lambda c: (c.topic, c.values))
    return out
