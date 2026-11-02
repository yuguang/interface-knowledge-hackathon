"""Print a fixed-seed random sample of the emitted conflicts, for the precision audit (Task 3.3).

Reads an already-ingested KB (default: the one the last harness run left in submission/.cache)
and rebuilds conflicts from it, so no re-ingest is needed after a change to kb/conflicts.py.

    python dev/sample_conflicts.py                    # 30 conflicts, seed 33
    python dev/sample_conflicts.py --n 0              # all conflicts
    python dev/sample_conflicts.py --score            # also score against dev/conflicts_gold.json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "submission"))
sys.path.insert(0, str(ROOT / "harness"))

from kb import conflicts as conflicts_mod  # noqa: E402
from kb.snapshots import SnapshotRegistry  # noqa: E402
from kb.store import Store  # noqa: E402


def _per_snapshot(store: Store, keys: tuple) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for o in sorted((o for k in keys for o in store.facts(fact_key=k)), key=lambda o: (o.taken_on, o.url)):
        vals = out.setdefault(o.snapshot_id[:2], [])
        if o.value_norm not in vals:
            vals.append(o.value_norm)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "submission" / ".cache" / "kb.sqlite"))
    ap.add_argument("--n", type=int, default=30, help="sample size; 0 = all")
    ap.add_argument("--seed", type=int, default=33)
    ap.add_argument("--score", action="store_true", help="score all conflicts vs dev/conflicts_gold.json")
    ap.add_argument("--quotes", action="store_true", help="print the per-value source quote")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        sys.exit(f"no KB at {db}: run the harness (dev/score_run.sh) first")
    store = Store(str(db))
    registry = SnapshotRegistry()
    for sid, taken in sqlite3.connect(str(db)).execute("SELECT snapshot_id, taken_on FROM snapshot"):
        registry.add(sid, taken)

    # keep the fact keys next to each conflict: build_conflicts emits topics, the audit wants keys
    keyed = conflicts_mod.build_conflicts_detailed(store, registry)
    print(f"{len(keyed)} conflicts emitted")
    if args.score:
        from scoring import score_conflicts
        gold = json.loads((ROOT / "dev" / "conflicts_gold.json").read_text())
        print(json.dumps(score_conflicts(gold, [c for _, _, c in keyed])))

    rows = list(keyed)
    if args.n and args.n < len(rows):
        rows = random.Random(args.seed).sample(rows, args.n)
    for i, (key, members, c) in enumerate(rows, 1):
        print(f"\n[{i}] {key}\n    topic: {c.topic}")
        if len(members) > 1:
            print(f"    merged keys: {', '.join(members)}")
        for sid, vals in sorted(_per_snapshot(store, members).items()):
            print(f"    s{sid}: {' / '.join(vals)}")
        print(f"    values: {c.values}  resolution: {c.resolution}  reason: {c.reason}")
        if args.quotes:
            for v, s in zip(c.values, c.sources):
                print(f"      {v!r} <- {s.snapshot_id[:2]} {s.url} :: {s.quote[:160]!r}")


if __name__ == "__main__":
    main()
