# A knowledge system that knows what's true now

This system answers questions about a credit union's website from five crawled snapshots. It gives the
current value, the right tier, a source, and an honest "isn't listed" when the site doesn't say.

- The solution is in **`submission/`** (`submission/solution.py` → `build()`).
- How it decides what is current, the API keys it needs, and the paid-API disclosure:
  **[`submission/NOTES.md`](submission/NOTES.md)**.

## Setup

Run every command from the repository root.

1. **Python 3.11 virtual environment**
   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```
2. **Install dependencies** (pinned versions)
   ```bash
   pip install -r submission/requirements.txt
   ```
3. **Snapshots.** The snapshot data is not in git. Put the five zips in `snapshots_zip/` at the repo
   root:
   ```
   snapshots_zip/01_2026-05-12_baseline.zip
   snapshots_zip/02_2026-05-19_recrawl.zip
   snapshots_zip/03_2026-06-09_recrawl.zip
   snapshots_zip/04_2026-08-27_rebaseline.zip
   snapshots_zip/05_2026-09-28_latest.zip
   ```
   You don't need to unzip them. On the first run, `harness/run_eval.py` unpacks them into
   `snapshots/` and skips any snapshot folder that already has a `manifest.json`.
4. **API key.** The system calls Claude (Haiku 5.5 and Sonnet 5.5) through the Anthropic API:
   ```bash
   export ANTHROPIC_API_KEY=...
   ```
   Without a key, or with `KB_NO_LLM=1`, the system runs in a rules-only fallback mode at lower
   accuracy. See `submission/NOTES.md`.

## Run

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

- Add `--judge` to blend in the harness's LLM judge for free-text items. It also needs
  `ANTHROPIC_API_KEY`.
- Add `--conflicts-gold dev/conflicts_gold.json` to score `conflicts()` against our dev gold.
- Results go to `results/` (`report.json`, `per_question.jsonl`).

What to expect on the first run from a fresh clone:
- The harness unpacks the zips. Chroma downloads its ONNX embedding model (all-MiniLM-L6-v2, about
  80 MB) into `~/.cache/chroma/` if this machine doesn't have it yet.
- Ingest makes Claude Haiku calls for prose facts, capped at 300 s of LLM time per snapshot.
- Cold ingest time depends on Anthropic API latency.
- In our last local scored run, ingest took 387.6 s. That figure is from a local run's `results/`
  report, which is not committed. A cold fresh clone has not been timed yet. The harness's 30-minute
  ingest limit is a hard cap.
- LLM responses are cached in `submission/.cache/llm.sqlite`. Every run rebuilds the fact store and the
  Chroma index in `submission/.cache/`, including re-embedding every chunk.
- A warm second run is therefore not much faster. Local runs that reused the LLM cache took 376–456 s
  to ingest, which misses the plan's "under 2 min warm" target.

Latest local scores (`dev/scores.csv`, public eval with `--judge`):
- QA 0.790 (the starter scores 0.093).
- Conflicts F1 0.784 on our dev gold.
- Final score 0.787, which includes the conflicts pillar scored against our dev gold. This is from a
  local run's `results/` report, which is not committed; `dev/scores.csv` has no final-score column.
- p95 latency 3.11 s.

## Repository layout

| Path | What |
|---|---|
| `submission/` | The solution: `solution.py`, `kb/` package, `requirements.txt`, `NOTES.md`, `data/facts_cache.jsonl` (LLM-extraction cache, **currently empty; build deferred**, so prose facts are extracted live at ingest). |
| `harness/`, `eval/`, `starter/` | The organizers' kit (unchanged). |
| `tests/` | Unit and integration tests (after setup, `pip install -r requirements-dev.txt`, then `pytest`). |
| `dev/` | Dev fixtures (paraphrase, as_of, refusal sets; conflicts gold), `score_run.sh`, `clean_env_check.sh`, `scores.csv`. |
| `snapshots/README.md` | The kit's description of what changed between snapshots (the snapshot data itself is git-ignored). |

## Checking a clean environment

`dev/clean_env_check.sh` follows the steps above in a temp dir:
1. a fresh `git clone`;
2. a new 3.11 venv and `pip install`;
3. copying the zips;
4. the eval with `--judge`, run cold and then warm.

It prints the ingest time, p95 and max latency for both runs. Two checks are expected to FAIL with the
current code:
- warm ingest under 120 s (see above);
- p95 of 1.5 s or less (currently 3.11 s). `dev/clean_env_check.sh --preflight`
runs only the cheap part: prerequisites, clone, zip copy and the harness's auto-unpack.
