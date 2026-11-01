"""Page classifier (stub; Track B replaces)."""
from __future__ import annotations

from .model import PageInfo


def classify_page(url: str, title: str, markdown: str, taken_on: str) -> PageInfo:
    return PageInfo("other", 40, None)
