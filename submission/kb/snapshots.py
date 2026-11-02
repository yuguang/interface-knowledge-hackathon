"""Snapshot registry and as_of date handling."""
from __future__ import annotations

import calendar
import re
from typing import Optional


class SnapshotRegistry:
    def __init__(self) -> None:
        self._taken: dict[str, str] = {}

    def add(self, snapshot_id: str, taken_on: str) -> None:
        self._taken[snapshot_id] = taken_on

    def ids(self) -> list[str]:
        return sorted(self._taken, key=lambda s: (self._taken[s], s))

    def latest(self) -> str:
        return self.ids()[-1]

    def taken_on(self, snapshot_id: str) -> str:
        return self._taken[snapshot_id]

    def resolve(self, as_of: Optional[str]) -> str:
        """None -> latest; before first -> first; else latest snapshot with taken_on <= as_of."""
        ids = self.ids()
        if as_of is None:
            return ids[-1]
        best = ids[0]
        for sid in ids:
            if self._taken[sid] <= as_of[:10]:
                best = sid
        return best


_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MON = r"(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"


_ISO = r"(?P<iso>(?P<iy>\d{4})-(?P<im>\d{2})-(?P<id>\d{2}))\b"
_MDY = (r"(?P<dmon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+"
        r"(?P<dday>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<dyear>\d{4})\b")
_MY = r"(?P<mmon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s*,?\s+(?P<myear>\d{4})\b"
_DATE = rf"(?:{_ISO}|{_MDY}|{_MY})"
# Ruling R15: a date in the question is an as_of only when anchored by a temporal phrase.
_ANCHORED = re.compile(rf"\b(?:as\s+of|as\s+it\s+stood(?:\s+(?:on|in))?|back\s+in)\s+{_DATE}")
_IN_MONTH = re.compile(rf"\bin\s+{_MY}")
# "closing in March 2026", "graduated in May 2026": the date is the event's, not an as_of.
_EVENT_VERB = re.compile(
    r"\b(?:clos(?:e|es|ed|ing)|open(?:s|ed|ing)?|launch(?:es|ed|ing)?|releas(?:e|es|ed|ing)|"
    r"graduat(?:e|es|ed|ing)|expir(?:e|es|ed|ing)|end(?:s|ed|ing)?|start(?:s|ed|ing)?|"
    r"beg(?:in|ins|an|inning)|mov(?:e|es|ed|ing)|relocat(?:e|es|ed|ing)|merg(?:e|es|ed|ing)|"
    r"announc(?:e|es|ed|ing)|retir(?:e|es|ed|ing)|publish(?:es|ed|ing)?|born|hired|join(?:s|ed|ing)?|"
    r"chang(?:e|es|ed|ing)|discontinu(?:e|es|ed|ing)|introduc(?:e|es|ed|ing))\b")


def _to_iso(m: re.Match) -> str:
    g = m.groupdict()
    if g.get("iso"):
        return g["iso"]
    if g.get("dmon"):
        return f"{int(g['dyear']):04d}-{_MONTHS[g['dmon']]:02d}-{int(g['dday']):02d}"
    y, mo = int(g["myear"]), _MONTHS[g["mmon"]]
    return f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"


def parse_question_date(question: str) -> Optional[str]:
    """Date the question asks about, or None (ruling R15).

    Accepted only after "as of", "as it stood", "back in", or as "in <Month YYYY>" when the
    words just before "in" are not an event verb. Bare dates ("closing March 31, 2026",
    "May 2026 graduates", "the May 2026 press release") are not an as_of.
    """
    q = question.lower()
    m = _ANCHORED.search(q)
    if m:
        return _to_iso(m)
    for m in _IN_MONTH.finditer(q):
        before = q[:m.start()].split()[-2:]
        if not any(_EVENT_VERB.fullmatch(w.strip(".,;:!?()\"'")) for w in before):
            return _to_iso(m)
    return None


def effective_as_of(question: str, as_of: Optional[str]) -> Optional[str]:
    return as_of if as_of else parse_question_date(question)
