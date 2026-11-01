#!/usr/bin/env bash
# Score the submission on the public eval and on the dev fixtures; append a row to dev/scores.csv.
#
#   dev/score_run.sh <label> [--no-judge]
#   PY=/path/to/python dev/score_run.sh facts          # PY defaults to `python`
#   KB_NO_LLM=1 dev/score_run.sh stub --no-judge       # free run (no judge, no LLM)
#
# public run : eval/public_eval.jsonl + --conflicts-gold dev/conflicts_gold.json (+ --judge)
# dev run    : public items (group := id, so each pairs with its paraphrase) + paraphrase,
#              as_of and refusal fixtures. `heldout` = weighted item score over the dev
#              fixture items only (public items are there only to complete paraphrase pairs).
# Row: commit,public,heldout,conflicts_f1,p95  (commit = <label>@<short sha>; p95 from the public run)
set -euo pipefail

LABEL="${1:?usage: dev/score_run.sh <label> [--no-judge]}"
JUDGE="--judge"
[[ "${2:-}" == "--no-judge" ]] && JUDGE=""
PY="${PY:-python}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/results/score_run/$LABEL"
mkdir -p "$OUT"
cd "$ROOT"

DEV_EVAL="$OUT/dev_eval.jsonl"
"$PY" - "$DEV_EVAL" <<'EOF'
import json, sys
rows = []
for line in open("eval/public_eval.jsonl"):
    if line.strip():
        it = json.loads(line)
        it["group"] = it["group"] or it["id"]
        rows.append(it)
for name in ("paraphrase_eval", "asof_eval", "refusal_eval"):
    rows += [json.loads(l) for l in open(f"dev/{name}.jsonl") if l.strip()]
open(sys.argv[1], "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
EOF

echo "== public ($LABEL) ${JUDGE:-no judge}"
"$PY" harness/run_eval.py --submission submission --snapshots snapshots --eval eval/public_eval.jsonl \
    --conflicts-gold dev/conflicts_gold.json $JUDGE --out "$OUT/public"
echo "== dev fixtures ($LABEL) ${JUDGE:-no judge}"
"$PY" harness/run_eval.py --submission submission --snapshots snapshots --eval "$DEV_EVAL" \
    $JUDGE --out "$OUT/dev"

ROW="$("$PY" - "$OUT" "$LABEL@$(git rev-parse --short HEAD)" <<'EOF'
import json, sys
out, commit = sys.argv[1], sys.argv[2]
pub = json.load(open(f"{out}/public/report.json"))
public_ids = {json.loads(l)["id"] for l in open("eval/public_eval.jsonl") if l.strip()}
dev = [json.loads(l) for l in open(f"{out}/dev/per_question.jsonl") if l.strip()]
dev = [r for r in dev if r["id"] not in public_ids]
heldout = sum(r["score"] * r["weight"] for r in dev) / (sum(r["weight"] for r in dev) or 1.0)
cons = json.load(open(f"{out}/dev/report.json"))["consistency"]
print(f"paraphrase_consistency={cons['paraphrase_consistency']} repeat={cons['repeat_consistency']}", file=sys.stderr)
f1 = (pub.get("conflicts") or {}).get("f1", "")
print(f"{commit},{pub['qa_score']:.3f},{heldout:.3f},{f1},{pub['speed']['p95_s']}")
EOF
)"
echo "$ROW" >> dev/scores.csv
echo "appended to dev/scores.csv: $ROW"
