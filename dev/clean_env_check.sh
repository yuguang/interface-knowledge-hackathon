#!/usr/bin/env bash
# Clean-environment check: follow README.md word for word on a fresh clone, then report timings.
#
#   dev/clean_env_check.sh                 # full check: clone, new 3.11 venv, install, eval --judge (cold + warm)
#   dev/clean_env_check.sh --preflight     # cheap: prerequisites, clone, zip copy, harness auto-unpack; no venv/eval
#   dev/clean_env_check.sh --summarize R   # print ingest / p95 / max checks for an existing report.json
#
# Environment:
#   REF=<commit>        commit to check (default: HEAD of this checkout)
#   PY311=<python>      Python 3.11 interpreter used to create the venv (default: python3.11)
#   ZIPS=<dir>          folder holding the five snapshot zips (default: <this repo>/snapshots_zip)
#   KEEP=1              keep the temp dir afterwards (default: deleted)
#   ANTHROPIC_API_KEY   required for the full check (the system and --judge both call the API)
#
# Full check, step by step (README.md "Setup" and "Run"):
#   1. git clone this repo into a temp dir and check out REF
#   2. $PY311 -m venv .venv ; pip install -r submission/requirements.txt
#   3. copy the five zips into snapshots_zip/ at the clone root (snapshots/ is NOT pre-created:
#      the harness must unpack the zips itself)
#   4. cold run:  python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl --judge
#      warm run:  the same command again in the same clone (LLM cache in submission/.cache/ now warm)
#   5. print ingest seconds, p95 and max latency, and check them against the targets below
# Targets (plan Task 3.4 Step 3): ingest < 600 s cold, < 120 s warm; p95 <= 1.5 s; max < 10 s.
# Exit status: 0 when every run completed and every target is met, 1 otherwise.
# "Cold" means a fresh clone: empty submission/.cache/ (LLM cache, SQLite, Chroma index). Chroma's ONNX
# embedding model lives in ~/.cache/chroma/ and is downloaded only if this machine lacks it.
# Disk: the full check needs roughly 1.5 GB free (venv with chromadb/onnxruntime, ~32 MB unpacked
# snapshots, SQLite + Chroma index under submission/.cache/).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
REF="${REF:-$(git -C "$SRC" rev-parse HEAD)}"
PY311="${PY311:-python3.11}"
ZIPS="${ZIPS:-$SRC/snapshots_zip}"
EXPECTED_ZIPS=(01_2026-05-12_baseline 02_2026-05-19_recrawl 03_2026-06-09_recrawl
               04_2026-08-27_rebaseline 05_2026-09-28_latest)

# summarize <report.json> <label> <ingest limit s>: print the numbers and PASS/FAIL lines; return 1 on any FAIL.
summarize() {
    "$PY311" - "$1" "$2" "$3" <<'EOF'
import json, sys
path, label, ingest_limit = sys.argv[1], sys.argv[2], float(sys.argv[3])
r = json.load(open(path))
sp = r["speed"]
print(f"[{label}] ingest {r['ingest_seconds']} s | p50 {sp['p50_s']} s | p95 {sp['p95_s']} s | max {sp['max_s']} s"
      f" | qa {r['qa_score']} | final {r['final_score']} | errors {r['errors']}")
checks = [
    (f"ingest < {ingest_limit:g} s", r["ingest_seconds"] < ingest_limit),
    ("ingest under the 30 min hard limit", not r["ingest_over_limit"]),
    ("p95 <= 1.5 s", sp["p95_s"] <= 1.5),
    ("max < 10 s", sp["max_s"] < 10),
    ("no answer errors", r["errors"] == 0),
]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
EOF
}

if [[ "${1:-}" == "--summarize" ]]; then
    summarize "${2:?usage: dev/clean_env_check.sh --summarize <report.json>}" report 600
    exit 0
fi

MODE=full
[[ "${1:-}" == "--preflight" ]] && MODE=preflight

# Prerequisites
"$PY311" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version' \
    || { echo "PY311=$PY311 is not Python 3.11" >&2; exit 1; }
for z in "${EXPECTED_ZIPS[@]}"; do
    [[ -f "$ZIPS/$z.zip" ]] || { echo "missing $ZIPS/$z.zip" >&2; exit 1; }
done
if [[ "$MODE" == full && -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ANTHROPIC_API_KEY is not set (needed by the system and by --judge)" >&2
    exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/kb-clean-env.XXXXXX")"
if [[ "${KEEP:-0}" == 1 ]]; then
    echo "temp dir kept: $TMP"
else
    trap 'rm -rf "$TMP"' EXIT
fi
REPO="$TMP/repo"

echo "== 1. fresh clone of $SRC at $REF"
git clone --quiet "$SRC" "$REPO"
git -C "$REPO" checkout --quiet --detach "$REF"
for f in submission/solution.py submission/requirements.txt harness/run_eval.py eval/public_eval.jsonl \
         README.md submission/NOTES.md; do
    [[ -f "$REPO/$f" ]] || { echo "clone is missing $f" >&2; exit 1; }
done
if git -C "$REPO" ls-files | grep -E "^snapshots/0|^snapshots_zip/|\.zip$"; then
    echo "snapshot data is tracked in git (listed above)" >&2
    exit 1
fi

echo "== 3. copy the snapshot zips into snapshots_zip/"
mkdir -p "$REPO/snapshots_zip"
for z in "${EXPECTED_ZIPS[@]}"; do
    cp -L "$ZIPS/$z.zip" "$REPO/snapshots_zip/"
done

if [[ "$MODE" == preflight ]]; then
    echo "== preflight: harness auto-unpack (stdlib only, no venv)"
    (cd "$REPO" && "$PY311" -I - <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "harness")
from run_eval import load_snapshots
snaps = load_snapshots(Path("snapshots"))
print(f"unpacked and loaded {len(snaps)} snapshots: " + ", ".join(f"{s.snapshot_id} ({len(s.pages)} pages)" for s in snaps))
assert len(snaps) == 5, len(snaps)
EOF
    )
    echo "preflight OK (no venv, install or eval was run)"
    exit 0
fi

echo "== 2. new Python 3.11 venv + pip install -r submission/requirements.txt"
"$PY311" -m venv "$REPO/.venv"
"$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
"$REPO/.venv/bin/python" -m pip install --quiet -r "$REPO/submission/requirements.txt"

cd "$REPO"
unset KB_NO_LLM KB_DB_PATH KB_LLM_CACHE
STATUS=0
for run in cold warm; do
    echo "== 4. $run run"
    t0=$(date +%s)
    if ! .venv/bin/python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl \
            --judge --out "results/clean_env/$run" > "$TMP/$run.log" 2>&1; then
        echo "$run run failed; last lines of the log:" >&2
        tail -30 "$TMP/$run.log" >&2
        exit 1
    fi
    echo "   wall time $(( $(date +%s) - t0 )) s (log: $TMP/$run.log)"
done

echo "== 5. timings"
summarize results/clean_env/cold/report.json cold 600 || STATUS=1
summarize results/clean_env/warm/report.json warm 120 || STATUS=1
exit "$STATUS"
