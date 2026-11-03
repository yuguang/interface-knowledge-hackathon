"""Reporting periods: which reporting year a question asks about, resolved without the wall clock.

The reference date (what "last year" is relative to) is, in order: the explicit `as_of` argument, a snapshot date
named in the question (snapshots.parse_question_date), else the latest ingested snapshot's taken-on date. The
reporting year never influences snapshot selection (that stays `effective_as_of` + registry resolution); it is only
a year qualifier on year-keyed metrics, today the annual VTO employee counts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional

from . import keys
from .model import Intent
from .snapshots import _ANCHORED, _IN_MONTH, parse_question_date

VTO_WHAT = "vto-employees"
_YEAR = re.compile(r"(?<![\d$.,-])((?:19|20)\d\d)(?![\d%,-]|\.\d)")
_LAST_YEAR = re.compile(r"\b(?:last|previous|prior) (?:calendar |reporting )?year\b")
_THIS_YEAR = re.compile(r"\b(?:this|current) (?:calendar |reporting )?year\b")


@dataclass(frozen=True)
class ReportingPeriod:
    year: Optional[str]  # None: no year requested (existing latest-year selection applies)
    reference_date: str  # ISO date


def resolve_reporting_period(question: str, as_of: Optional[str], latest_taken_on: str) -> ReportingPeriod:
    ref = as_of[:10] if as_of else (parse_question_date(question) or latest_taken_on[:10])
    # a date that anchors the snapshot ("as of March 2025") is not a reporting year
    low = _IN_MONTH.sub(" ", _ANCHORED.sub(" ", question.lower()))
    years = sorted(set(_YEAR.findall(low)))
    if len(years) == 1:
        return ReportingPeriod(years[0], ref)
    if years:  # several years named: ambiguous, so no single reporting year
        return ReportingPeriod(None, ref)
    if _LAST_YEAR.search(low):
        return ReportingPeriod(str(int(ref[:4]) - 1), ref)
    if _THIS_YEAR.search(low):
        return ReportingPeriod(ref[:4], ref)
    return ReportingPeriod(None, ref)


def is_vto_count(intent: Intent) -> bool:
    return (intent.product, intent.attribute) == ("org", "count") and (intent.qualifiers or {}).get("what") == VTO_WHAT


def apply_reporting_period(intent: Intent, period: ReportingPeriod) -> Intent:
    """The VTO count intent gets the requested year as a qualifier and in its memo key; others are returned as is."""
    if not is_vto_count(intent) or not period.year or "year" in intent.qualifiers:
        return intent
    old = keys.make_key(intent.product, intent.attribute, intent.qualifiers)
    quals = dict(intent.qualifiers, year=period.year)
    new = keys.make_key(intent.product, intent.attribute, quals)
    return replace(intent, qualifiers=quals, canonical=intent.canonical.replace(old, new, 1))
