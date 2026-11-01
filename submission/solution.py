"""Submission entry point: the harness imports this and calls build()."""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(next(d / "harness" for d in _HERE.parents if (d / "harness" / "contract.py").exists())))

from kb.system import KnowledgeSystem, build as _build  # noqa: E402


def build() -> KnowledgeSystem:
    return _build()
