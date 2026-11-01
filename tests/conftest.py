from pathlib import Path

import pytest

from kb.snapshots import SnapshotRegistry
from run_eval import load_snapshots

ROOT = Path(__file__).resolve().parent.parent
MINI = Path(__file__).resolve().parent / "fixtures" / "mini_site"
REAL = ROOT / "snapshots"

ALL5 = [
    ("01_2026-05-12_baseline", "2026-05-12"),
    ("02_2026-05-19_recrawl", "2026-05-19"),
    ("03_2026-06-09_recrawl", "2026-06-09"),
    ("04_2026-08-27_rebaseline", "2026-08-27"),
    ("05_2026-09-28_latest", "2026-09-28"),
]


def mini_snapshots():
    return load_snapshots(MINI)


def real_snapshots():
    if not (REAL / "05_2026-09-28_latest").exists():
        pytest.skip("real snapshots not present")
    return load_snapshots(REAL)


@pytest.fixture
def registry_all5():
    reg = SnapshotRegistry()
    for sid, d in ALL5:
        reg.add(sid, d)
    return reg
