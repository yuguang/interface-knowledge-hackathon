"""Fact resolution and intent selection (stub; Track E replaces)."""
from __future__ import annotations

from .model import Intent, ResolvedFact
from .store import Store


def resolve_snapshot(store: Store, snapshot_id: str) -> list[ResolvedFact]:
    return []


def select_for_intent(intent: Intent, resolved: list[ResolvedFact]) -> list[ResolvedFact]:
    return []


def stale_values(store: Store, selected: list[ResolvedFact]) -> set[str]:
    return set()
