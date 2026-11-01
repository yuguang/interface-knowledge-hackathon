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


def parse_question_date(question: str) -> Optional[str]:
    q = question.lower()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q)
    if m:
        return m.group(0)
    m = re.search(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", q)
    if m:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    m = re.search(rf"\b{_MON}\s*,?\s+(\d{{4}})\b", q)
    if m:
        y, mo = int(m.group(2)), _MONTHS[m.group(1)]
        return f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"
    return None


def effective_as_of(question: str, as_of: Optional[str]) -> Optional[str]:
    return as_of if as_of else parse_question_date(question)
