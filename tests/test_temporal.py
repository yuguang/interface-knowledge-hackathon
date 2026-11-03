import datetime

import pytest

from kb import intent as intent_mod
from kb.model import Intent
from kb.snapshots import SnapshotRegistry, effective_as_of
from kb.temporal import ReportingPeriod, apply_reporting_period, resolve_reporting_period

LATEST = "2026-09-28"


@pytest.mark.parametrize("question,as_of,latest,year,ref", [
    ("How many employees used VTO in 2023?", None, LATEST, "2023", LATEST),
    ("How many employees used VTO in 2024?", None, LATEST, "2024", LATEST),
    ("How many employees used VTO last year?", "2024-12-31", LATEST, "2023", "2024-12-31"),
    ("How many employees used VTO last year?", "2025-01-01", LATEST, "2024", "2025-01-01"),
    ("How many employees used VTO last year?", "2025-12-31", LATEST, "2024", "2025-12-31"),
    ("How many employees used VTO last year?", None, LATEST, "2025", LATEST),
    ("How many employees used VTO?", None, LATEST, None, LATEST),
    ("How many employees used VTO in 2023?", "2025-12-31", LATEST, "2023", "2025-12-31"),
    # a snapshot date named in the question is the reference, and its year is not a reporting year
    ("As of March 2025, how many employees used VTO last year?", None, LATEST, "2024", "2025-03-31"),
    ("As of June 2025, how many employees used VTO?", None, LATEST, None, "2025-06-30"),
    # the explicit as_of argument beats a date in the question
    ("As of March 2025, how many used VTO last year?", "2026-01-15", LATEST, "2025", "2026-01-15"),
])
def test_reporting_period(question, as_of, latest, year, ref):
    assert resolve_reporting_period(question, as_of, latest) == ReportingPeriod(year, ref)


def test_explicit_year_beats_relative_and_two_years_are_ambiguous():
    assert resolve_reporting_period("VTO use in 2023 versus last year", "2025-06-01", LATEST).year == "2023"
    assert resolve_reporting_period("VTO use in 2023 and 2024", None, LATEST).year is None


def test_wall_clock_cannot_change_result(monkeypatch):
    before = resolve_reporting_period("VTO last year", "2024-12-31", LATEST)

    class Frozen(datetime.date):
        @classmethod
        def today(cls):
            return cls(1999, 1, 1)

    monkeypatch.setattr(datetime, "date", Frozen)
    assert resolve_reporting_period("VTO last year", "2024-12-31", LATEST) == before


def _vto(quals=None):
    base = {"what": "vto-employees"}
    return Intent("fact", "org", "count", dict(base, **(quals or {})), None, "q",
                  "org|count|what=vto-employees|fact|")


def test_apply_sets_year_qualifier_and_key():
    out = apply_reporting_period(_vto(), ReportingPeriod("2023", LATEST))
    assert out.qualifiers == {"what": "vto-employees", "year": "2023"}
    assert out.canonical == "org|count|what=vto-employees;year=2023|fact|"
    assert out.canonical != apply_reporting_period(_vto(), ReportingPeriod("2024", LATEST)).canonical


def test_apply_without_year_leaves_intent_for_latest_year_selection():
    it = _vto()
    assert apply_reporting_period(it, ReportingPeriod(None, LATEST)) == it


def test_apply_leaves_unrelated_intents_alone():
    branches = Intent("fact", "org", "count", {"what": "branches"}, None, "q", "org|count|what=branches|fact|")
    award = Intent("fact", "org", "award", {}, None, "q", "org|award||fact|")
    for it in (branches, award, intent_mod.parse("How many employees does Dupaco have last year?")):
        assert apply_reporting_period(it, ReportingPeriod("2024", LATEST)) is it


def test_reporting_year_never_alters_snapshot_selection():
    reg = SnapshotRegistry()
    reg.add("a", "2026-05-12")
    reg.add("b", LATEST)
    for q in ("How many employees used VTO in 2023?", "How many employees used VTO last year?",
              "How many employees used VTO?"):
        assert effective_as_of(q, None) is None
        assert reg.resolve(effective_as_of(q, None)) == "b"
        assert reg.resolve(effective_as_of(q, "2026-05-15")) == "a"
    assert reg.resolve(effective_as_of("As of May 2026, VTO in 2023?", None)) == "a"
