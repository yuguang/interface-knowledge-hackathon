"""Build submission/data/facts_cache.jsonl: Haiku prose facts per in-scope chunk.

Uses the same normalize/classify/chunk path as KnowledgeSystem ingest so chunk hashes match at ingest.
Cache lines hold derived values only: {"chunk_hash", "prompt_ver", "facts"} (no quote, no page text).

  python dev/build_facts_cache.py --snapshots snapshots --dry-run   # count in-scope chunks, no LLM
  python dev/build_facts_cache.py --snapshots snapshots --limit 20  # at most 20 LLM calls
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "submission"), str(ROOT / "harness")]

from kb import classify, normalize  # noqa: E402
from kb.extract import llm_facts  # noqa: E402
from kb.llm import LLM  # noqa: E402
from kb.system import _fallback_chunks  # noqa: E402
from run_eval import load_snapshots  # noqa: E402


def in_scope_chunks(snapshots) -> dict[str, tuple[str, str, str]]:
    """chunk_hash -> (text, title, heading_path) for every in-scope chunk (deduped across snapshots)."""
    found: dict[str, tuple[str, str, str]] = {}
    for snap in snapshots:
        try:
            bp = normalize.fit_boilerplate(snap.pages)
        except Exception:
            bp = frozenset()
        for page in sorted(snap.pages, key=lambda p: p.url):
            info = classify.classify_page(page.url, page.title, page.markdown, snap.taken_on)
            try:
                md = normalize.clean(page.markdown, bp)
            except Exception:
                md = page.markdown
            try:
                chunks = normalize.chunk_page(page.url, page.title, md)
            except Exception:
                chunks = _fallback_chunks(page.url, page.title, md)
            for ch in chunks:
                if llm_facts.in_scope(info.page_type, ch.text):
                    found.setdefault(ch.hash, (ch.text, ch.title, ch.heading_path))
    return found


def read_cache(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["chunk_hash"]] = r
    return rows


def write_cache(path: Path, rows: dict[str, dict]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for h in sorted(rows):
            f.write(json.dumps({"chunk_hash": h, "prompt_ver": rows[h]["prompt_ver"], "facts": rows[h]["facts"]},
                               ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, default=ROOT / "snapshots")
    ap.add_argument("--limit", type=int, default=None, help="max LLM calls")
    ap.add_argument("--dry-run", action="store_true", help="print in-scope chunk count; no LLM calls")
    ap.add_argument("--out", type=Path, default=llm_facts.CACHE_PATH)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)

    chunks = in_scope_chunks(load_snapshots(a.snapshots))
    print(f"in-scope chunks: {len(chunks)}")
    if a.dry_run:
        return 0
    rows = read_cache(a.out)
    todo = [h for h in sorted(chunks) if h not in rows]
    if a.limit is not None:
        todo = todo[:a.limit]
    llm = LLM(ROOT / "submission" / ".cache" / "llm.sqlite")
    if not llm.enabled:
        print("LLM disabled (no ANTHROPIC_API_KEY or KB_NO_LLM=1)", file=sys.stderr)
        return 1

    def work(h):
        text, title, heading = chunks[h]
        return h, llm_facts.ask(llm, text, title, heading)

    ok = bad = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for h, facts in ex.map(work, todo):
            if facts is None:
                bad += 1  # failed calls are never cached
            else:
                rows[h] = {"prompt_ver": llm_facts.PROMPT_VER, "facts": facts}
                ok += 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    write_cache(a.out, rows)
    print(f"calls ok={ok} failed={bad}; cache lines={len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
