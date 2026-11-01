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
QUALIFIERS = ("term", "tier", "loan_amount", "model_year", "segment", "kind", "name", "day", "what")


def make_key(product: str, attribute: str, qualifiers: dict[str, str]) -> str:
    bad = [q for q in qualifiers if q not in QUALIFIERS]
    if bad:
        raise ValueError(f"unknown qualifier(s): {bad}")
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


def norm_percent(s: str) -> Optional[str]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    if not m:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", s)
    if not m:
        return None
    num = m.group(1)
    if "." in num:
        whole, frac = num.split(".")
        frac = frac.rstrip("0")
        num = f"{whole}.{frac.ljust(2, '0')}"
    return num + "%"


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


def norm_term_months(s: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*-?\s*(months?|mos?\b|years?|yrs?\b)", s, re.I)
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


def norm_band(s: str) -> Optional[str]:
    pat = r"\$\s*(\d[\d,]*(?:\.\d+)?)" if "$" in s else r"(\d[\d,]*(?:\.\d+)?)"
    nums = [_amt(n) for n in re.findall(pat, s)]
    low = s.lower()
    if not nums:
        return None
    if len(nums) >= 2:
        if float(nums[0]) > float(nums[1]):
            return None
        return f"{nums[0]}-{nums[1]}"
    if "+" in s or re.search(r"and (up|over|above)|or more|and more|over|above|at least", low):
        return f"{nums[0]}-inf"
    if re.search(r"up to|under|less than|below|max", low):
        return f"0-{nums[0]}"
    return None


def norm_model_year(s: str) -> Optional[str]:
    low = s.lower()
    m = re.search(r"\b(\d{4})\s*-\s*(\d{2,4})\b", s)
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
    return y


def band_contains(band: str, amount: float) -> bool:
    lo, hi = band.split("-", 1)
    if amount < float(lo):
        return False
    return hi == "inf" or amount <= float(hi)
