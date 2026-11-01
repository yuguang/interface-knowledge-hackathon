"""Conflict builder (stub; Track F replaces)."""
from __future__ import annotations

from contract import Conflict

from .snapshots import SnapshotRegistry
from .store import Store


def build_conflicts(store: Store, registry: SnapshotRegistry) -> list[Conflict]:
    return []
