#!/usr/bin/env python3
"""Discovery pass over the snapshots: per-snapshot URL inventory and page-type histogram.

    python dev/discover.py [--snapshots snapshots] [--top 25] [--grep REGEX]

For each snapshot (oldest first) prints pages added / removed / changed versus the previous
snapshot, then a histogram of URL path prefixes (host + first path segment, or "pdf").
With --grep, prints every (snapshot, url, line) whose text matches REGEX: the tool used to
trace one topic across 01..05 for dev/topic_trace.md.

NOTE: "changed" compares a sha1 of the RAW page markdown. Track A's normalizer (boilerplate
removal) was not merged when this was written, so nav/footer churn counts as a change; switch
`page_hash` to the cleaned text once kb.normalize lands.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

from run_eval import load_snapshots  # noqa: E402


def page_hash(markdown: str) -> str:
    return hashlib.sha1(markdown.encode("utf-8")).hexdigest()  # raw hash (see module note)


def page_type(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    if p.path.lower().endswith(".pdf"):
        return f"{host} pdf"
    seg = [s for s in p.path.split("/") if s]
    return f"{host}/{seg[0]}" if seg else f"{host}/"


def inventory(snaps) -> list[dict]:
    rows, prev = [], {}
    for s in snaps:
        cur = {p.url: page_hash(p.markdown) for p in s.pages}
        rows.append({
            "snapshot": s.snapshot_id, "pages": len(cur),
            "added": sorted(set(cur) - set(prev)) if prev else [],
            "removed": sorted(set(prev) - set(cur)),
            "changed": sorted(u for u in set(cur) & set(prev) if cur[u] != prev[u]),
        })
        prev = cur
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, default=ROOT / "snapshots")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--list", action="store_true", help="list added/removed URLs")
    ap.add_argument("--grep", help="regex to trace across snapshots (case-insensitive)")
    args = ap.parse_args()

    snaps = load_snapshots(args.snapshots)
    if args.grep:
        rx = re.compile(args.grep, re.I)
        for s in snaps:
            for p in sorted(s.pages, key=lambda p: p.url):
                for line in p.markdown.splitlines():
                    if rx.search(line):
                        print(f"{s.snapshot_id[:2]}  {p.url}  |  {line.strip()[:220]}")
        return

    for r in inventory(snaps):
        print(f"{r['snapshot']}: {r['pages']} pages  +{len(r['added'])} -{len(r['removed'])} ~{len(r['changed'])}")
        if args.list:
            for u in r["added"]:
                print(f"   + {u}")
            for u in r["removed"]:
                print(f"   - {u}")
    print("\npage types (snapshot 05):")
    for t, n in Counter(page_type(p.url) for p in snaps[-1].pages).most_common(args.top):
        print(f"  {n:5d}  {t}")


if __name__ == "__main__":
    main()
