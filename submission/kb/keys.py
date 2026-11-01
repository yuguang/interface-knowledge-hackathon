"""Fact-key vocabulary and value normalisers."""
from __future__ import annotations

import re
from typing import Optional

PRODUCTS = ("auto_loan", "mortgage_fixed", "mortgage_arm", "heloc", "home_equity_loan", "personal_loan",
            "student_loan", "construction_loan", "rv_loan", "certificate", "certificate_special",
            "share_savings", "money_market", "checking", "bright_savings", "jump_start", "ira",
            "credit_card", "branch", "org", "program")
ATTRIBUTES = ("apr", "apy", "rate", "min_balance", "hours_lobby", "hours_driveup", "address", "phone",
              "status", "closure_date", "routing_number", "count", "url", "award", "eligibility", "detail")
QUALIFIERS = ("term", "tier", "loan_amount", "model_year", "segment", "kind", "name", "day", "what", "year")


_RESERVED = (";", "=", "|")


def make_key(product: str, attribute: str, qualifiers: dict[str, str]) -> str:
    if product not in PRODUCTS:
        raise ValueError(f"unknown product: {product!r}")
    if attribute not in ATTRIBUTES:
        raise ValueError(f"unknown attribute: {attribute!r}")
    bad = [q for q in qualifiers if q not in QUALIFIERS]
    if bad:
        raise ValueError(f"unknown qualifier(s): {bad}")
    bad = [k for k, v in qualifiers.items() if any(c in str(v) for c in _RESERVED)]
    if bad:
        raise ValueError(f"qualifier value(s) contain one of {_RESERVED}: {bad}")
    qs = ";".join(f"{k}={qualifiers[k]}" for k in sorted(qualifiers))
    return f"{product}|{attribute}|{qs}"


def parse_key(key: str) -> tuple[str, str, dict[str, str]]:
    product, attribute, qs = key.split("|", 2)
    quals = {}
    if qs:
        for part in qs.split(";"):
            k, v = part.split("=", 1)
            quals[k] = v
    return product, attribute, quals


_NUM = r"\d[\d,]*(?:\.\d+)?"
# A range separator: ASCII hyphen, en dash, em dash, or the word "to".
_SEP = r"\s*(?:-|\u2013|\u2014|\bto\b)\s*"


def norm_percent(s: str) -> Optional[str]:
    """Percent with at least 2 decimals: "4%" -> "4.00%", ".25%" -> "0.25%", "5.390 %" -> "5.39%"."""
    num_pat = r"(\d+(?:\.\d+)?|\.\d+)"
    m = re.search(num_pat + r"\s*%", s)
    if not m:
        m = re.fullmatch(r"\s*" + num_pat + r"\s*", s)
    if not m:
        return None
    whole, _, frac = m.group(1).partition(".")
    return f"{int(whole or '0')}.{frac.rstrip('0').ljust(2, '0')}%"


def _to_int(num: str) -> int:
    return int(float(num.replace(",", "")))


def norm_money(s: str) -> Optional[str]:
    m = re.search(r"\$\s*(\d[\d,]*)(?:\.(\d+))?", s)
    if not m:
        return None
    whole = f"{int(m.group(1).replace(',', '')):,}"
    cents = m.group(2)
    if cents and int(cents) != 0:
        return f"${whole}.{cents.ljust(2, '0')[:2]}"
    return f"${whole}"


_UNIT = r"(months?|mos?\b|years?|yrs?\b)"


def norm_term_months(s: str) -> Optional[int]:
    """One term in months. A range ("36-72 months", "1-5 years") is not one term -> None."""
    if re.search(r"\d+" + _SEP + r"\d+\s*" + _UNIT, s, re.I):
        return None
    m = re.search(r"(\d+)\s*-?\s*" + _UNIT, s, re.I)
    if not m:
        return None
    n = int(m.group(1))
    return n * 12 if m.group(2).lower().startswith("y") else n


def _amt(num: str) -> str:
    v = num.replace(",", "")
    if "." in v:
        w, f = v.split(".", 1)
        f = f.rstrip("0")
        return f"{int(w)}.{f}" if f else str(int(w))
    return str(int(v))


def _range(lo: str, hi: str) -> Optional[str]:
    lo, hi = _amt(lo), _amt(hi)
    return None if float(lo) > float(hi) else f"{lo}-{hi}"


def norm_band(s: str, minimum: bool = False) -> Optional[str]:
    """Amount band "lo-hi" / "lo-inf". With `minimum=True` a bare single amount is a minimum ("lo-inf")."""
    dollar = "$" in s
    m = re.search((r"\$\s*(" if dollar else r"(") + _NUM + r")" + _SEP + r"\$?\s*(" + _NUM + r")", s)
    if m:
        return _range(m.group(1), m.group(2))
    nums = re.findall(r"\$\s*(" + _NUM + r")" if dollar else r"(" + _NUM + r")", s)
    if not nums:
        return None
    if dollar and len(nums) >= 2:
        return _range(nums[0], nums[1])
    lo = _amt(nums[0])
    low = s.lower()
    if "+" in s or re.search(r"and (up|over|above)|or more|and more|over|above|at least", low):
        return f"{lo}-inf"
    if re.search(r"up to|under|less than|below|max", low):
        return f"0-{lo}"
    return f"{lo}-inf" if minimum else None


def norm_model_year(s: str) -> Optional[str]:
    """Model-year band: "2023\u201326 Model Year" -> "2023-2026", "2020 Model Year" -> "2020-2020"."""
    low = s.lower()
    m = re.search(r"\b(\d{4})" + _SEP + r"(\d{4}|\d{2})\b", s)
    if m:
        a, b = m.group(1), m.group(2)
        if len(b) == 2:
            b = a[:2] + b
        return f"{a}-{b}"
    m = re.search(r"\b(\d{4})\b", s)
    if not m:
        return None
    y = m.group(1)
    if re.search(r"older|and prior|or earlier|and earlier", low):
        return f"0-{y}"
    if re.search(r"newer|and later|or later", low):
        return f"{y}-9999"
    return f"{y}-{y}"


def band_contains(band: str, amount: float) -> bool:
    """Whether `amount` falls in `band`. Never raises: a malformed band contains nothing.

    Gap rule (R12): an upper bound written without cents covers everything strictly below hi+1
    ("10000-24999" contains 24999.99, not 25000); an upper bound with cents is inclusive at that value.
    """
    try:
        lo, hi = str(band).split("-", 1)
        if float(amount) < float(lo):
            return False
        if hi == "inf":
            return True
        if "." in hi:
            return float(amount) <= float(hi)
        return float(amount) < int(hi) + 1
    except (ValueError, TypeError):
        return False
