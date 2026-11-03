# Corroborating Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fact answers cite up to 2 additional current pages that show the same value as the winning page, so a correct answer also gets source credit when the grader expects the product page rather than `/rates`.

**Architecture:** The resolver (`resolve._pick`) currently keeps only the winner and the *disagreeing* observations (`losers`). It will also record the *agreeing* observations (same key, same value, other URL) on `ResolvedFact.agreeing`, and the store will persist them. `compose.render_facts` keeps the winner sources exactly as today, then appends at most 2 agreeing pages, ranked by topical fit to the question and then by authority. The answer text, the chosen value and `conflict_note` do not change.

**Tech Stack:** Python 3, SQLite (`kb/store.py`), pytest. Run tests with `.venv/bin/python -m pytest`.

**Spec:** Approved in chat on 2026-11-03 (no separate doc). Summary:
- When the answer's value also appears on other pages in the same snapshot, add up to 2 of those pages as extra citations, ranked by fit to the question's topic. The rate sheet (the winner) stays first.
- Only pages from the same snapshot with the *same* value. Never a page that disagrees. Never old blog/news pages.
- Answer text and value unchanged.
- Out of scope: dup-088 (the refusal stays, which the user confirmed), dup-005 (scorer limitation), dup-064 (the expected pages don't contain lobby hours, so it's deliberately not chased).
- Verify with unit tests, a public eval diffed per question against `results/fix-verification-20261103/public`, and the dev evals (paraphrase, refusal, as-of).

## Global Constraints

- Extra sources per answer: **at most 2** in total across all selected facts, on top of the winner sources.
- Eligible page types for extra sources: `AUTHORITY[page_type] >= AUTHORITY["other"]` (40), meaning rate_sheet, product, branch, disclosure, other. This excludes calculator, promo, blog, news and legacy. *(My call: the spec named blog/news/legacy. Promo and calculator are excluded too because they are the other page types the resolver already treats as untrustworthy.)*
- An extra source must have `snapshot_id == winner.snapshot_id` and `value_norm == resolved.value`.
- Winner sources come first, in today's order, deduplicated by URL as today. Extras never repeat a URL that is already cited.
- `Answer.text`, `Answer.conflict_note` and `Answer.confident` must be byte-identical to today's for the same input.
- Deterministic: the same inputs give the same source order (ties broken by URL).

## Review Focus

1. **Reused `submission/.cache/kb.sqlite` with the old schema.** `fact_resolved` has no `agreeing_json` column. Expected: the store adds the column on open, and old rows read back with `agreeing == []`. Test `test_resolved_legacy_row_without_agreeing` is in Task 1.
2. **Stale memoised answers.** `answer_memo` survives re-ingest, so a reused DB would keep serving the old single-source answers. Expected: `set_resolved(sid, …)` also deletes `answer_memo` rows for that `sid`. Test `test_set_resolved_clears_memo_for_snapshot` is in Task 1. *(My call: this is a small existing gap that would otherwise hide the change.)*
3. **Same page cited twice.** A page can produce two observations of one fact (cash-management lists the routing number twice), or the agreeing page can equal another fact's winner. Expected: each URL appears once in `sources`. Test `test_extra_sources_dedupe_urls` is in Task 2.
4. **A multi-fact list answer** (the certificate list, 16+ facts) has many agreeing pages. Expected: the total extras stay at 2, not 2 per fact. Test `test_extra_sources_capped_across_facts` is in Task 2.
5. **An agreeing observation from an older snapshot or with a different value**, which can occur if facts are built by hand or by a future caller. Expected: it is never cited. Test `test_extra_sources_same_snapshot_same_value_only` is in Task 2.

---

### Task 1: Record and persist agreeing observations

**Files:**
- Modify: `submission/kb/model.py` (`ResolvedFact`, ~line 78)
- Modify: `submission/kb/resolve.py` (`_pick`, lines 52-87)
- Modify: `submission/kb/store.py` (schema ~line 34, `Store.__init__` ~line 54, `set_resolved` / `resolved` lines 191-209)
- Test: `tests/test_resolve.py`, `tests/test_store.py`

**Interfaces:**
- Produces: `ResolvedFact.agreeing: list[FactObs]`, a new last field defaulting to `[]` via `field(default_factory=list)`. It holds every live observation with `o.value_norm == winner.value_norm and o.url != winner.url`, in `live` order, unfiltered by page type (filtering happens at render time). Existing positional constructions (`ResolvedFact(key, value, winner, losers, rule)`) keep working.
- Produces: `Store.resolved(...)` returns `ResolvedFact`s with `agreeing` populated from the new column `fact_resolved.agreeing_json TEXT` (`NULL` → `[]`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_resolve.py`:

```python
def test_pick_records_agreeing_observations():
    st = build([obs(K, "5.39%", url="https://x/rates"),
                obs(K, "5.39%", url="https://x/auto-loans", ptype="product"),
                obs(K, "5.39%", url="https://x/blog/post", ptype="blog"),
                obs(K, "5.34%", url="https://x/calc", ptype="calculator")])
    [r] = resolve_snapshot(st, "s5")
    assert r.winner.url == "https://x/rates"
    assert sorted(o.url for o in r.agreeing) == ["https://x/auto-loans", "https://x/blog/post"]
    assert [l.value_norm for l in r.losers] == ["5.34%"]


def test_pick_agreeing_excludes_winner_url():
    st = build([obs(K, "5.39%", url="https://x/rates"), obs(K, "5.39%", url="https://x/rates", quote="again")])
    [r] = resolve_snapshot(st, "s5")
    assert r.agreeing == []
```

In `tests/test_store.py`, next to `test_resolved_roundtrip`:

```python
def test_resolved_roundtrip_keeps_agreeing():
    st = store2()
    w, a = obs(), obs(url="u3", page_type="rate_sheet", authority=100)
    st.set_resolved(S1, [ResolvedFact(w.fact_key, w.value_norm, w, [], "single source", [a])])
    [r] = st.resolved(S1)
    assert r.agreeing == [a]


def test_resolved_legacy_row_without_agreeing(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE fact_resolved (fact_key TEXT, snapshot_id TEXT, value_norm TEXT, obs_id INTEGER, "
                "losers_json TEXT, winner_json TEXT, rule TEXT, PRIMARY KEY (fact_key, snapshot_id))")
    w = obs()
    con.execute("INSERT INTO fact_resolved VALUES (?,?,?,NULL,'[]',?,?)",
                (w.fact_key, S1, w.value_norm, json.dumps(asdict(w)), "single source"))
    con.commit(); con.close()
    [r] = Store(str(db)).resolved(S1)
    assert r.agreeing == [] and r.winner == w


def test_set_resolved_clears_memo_for_snapshot():
    st = store2()
    st.memo_put("k", S1, {"text": "old"})
    st.memo_put("k", S2, {"text": "keep"})
    st.set_resolved(S1, [])
    assert st.memo_get("k", S1) is None and st.memo_get("k", S2) == {"text": "keep"}
```

Add any missing imports (`json`, `asdict`, `ResolvedFact`, `Store`) to the test module.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_resolve.py tests/test_store.py -k "agreeing or legacy_row or clears_memo" -v`
Expected: FAIL (`AttributeError: 'ResolvedFact' object has no attribute 'agreeing'` / `TypeError` on the 6-arg constructor / memo still present).

- [ ] **Step 3: Implement**

- `model.py`: add `agreeing: list[FactObs] = field(default_factory=list)` as the last field of `ResolvedFact` (import `field`).
- `resolve._pick`: compute `agreeing` from `live` as defined in Interfaces, and pass it to `ResolvedFact`.
- `store.py`:
  - Add `agreeing_json TEXT` to the `fact_resolved` DDL.
  - In `Store.__init__`, after `executescript`, run `ALTER TABLE fact_resolved ADD COLUMN agreeing_json TEXT` when `PRAGMA table_info(fact_resolved)` lacks it.
  - `set_resolved` writes `json.dumps([asdict(o) for o in r.agreeing])` and runs `DELETE FROM answer_memo WHERE snapshot_id=?` for the same `snapshot_id` in the same commit.
  - `resolved` reads the column, treating `NULL` as `[]`.

- [ ] **Step 4: Run the tests to verify they pass, plus the existing suites they touch**

Run: `.venv/bin/python -m pytest tests/test_resolve.py tests/test_store.py tests/test_model.py tests/test_conflicts.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add submission/kb/model.py submission/kb/resolve.py submission/kb/store.py tests/test_resolve.py tests/test_store.py
git commit -m "feat(resolve): record same-value observations from other pages on ResolvedFact.agreeing"
```

---

### Task 2: Cite up to 2 corroborating pages in fact answers

**Files:**
- Modify: `submission/kb/compose.py` (`render_facts`, lines 198-212; add a helper above it)
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `ResolvedFact.agreeing` (Task 1).
- Produces: `_extra_sources(question: str, selected: list[ResolvedFact], cited: set[str], limit: int = 2) -> list[Source]` in `compose.py`. Candidates are the `agreeing` observations of each `r` in `selected` that pass the Global Constraints (authority ≥ `AUTHORITY["other"]`, same snapshot as `r.winner`, `value_norm == r.value`, URL not in `cited`). Deduplicate by URL. Sort by `(-fit, -authority, url)`, where `fit` = the number of shared word stems between (question + the fact's product slug with `_` read as a space) and the URL path. Use 5-letter stems of `[a-z]{3,}` words, the same rule as `resolve._stems`; import it rather than copying it. Return the first `limit` as `Source(o.url, o.snapshot_id, o.quote[:200])`.
- `render_facts` appends `_extra_sources(question, selected, seen)` after the existing winner loop. Nothing else in `render_facts` changes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_compose.py` (extend the `rf` helper with an `agreeing=()` parameter passed as the 6th `ResolvedFact` argument; import `AUTHORITY` and give `obs` an `authority=None` override that defaults to `AUTHORITY[page_type]`):

```python
AUTO = "https://www.dupaco.com/personal/loans/auto-motorcycle-loans"

def test_extra_source_added_after_rate_sheet():
    a = render_facts("What's the APR for a 60-month auto loan?", intent(),
                     [rf("5.39%", agreeing=[obs("5.39%", url=AUTO, page_type="product")])], SID)
    assert [s.url for s in a.sources] == ["https://www.dupaco.com/rates", AUTO]
    assert a.text == "The 60-month APR for 2024-2027 model-year vehicles is as low as 5.39% APR."
    assert a.conflict_note is None


def test_extra_sources_exclude_low_authority_pages():
    bad = [obs("5.39%", url=f"https://www.dupaco.com/{t}", page_type=t)
           for t in ("blog", "news", "legacy", "promo", "calculator")]
    a = render_facts("q", intent(), [rf("5.39%", agreeing=bad)], SID)
    assert [s.url for s in a.sources] == ["https://www.dupaco.com/rates"]


def test_extra_sources_ranked_by_topic_then_authority():
    ag = [obs("5.39%", url="https://www.dupaco.com/contact", page_type="branch"),
          obs("5.39%", url="https://www.dupaco.com/business/cash-management", page_type="product"),
          obs("5.39%", url=AUTO, page_type="product")]
    a = render_facts("auto loan APR", intent(), [rf("5.39%", agreeing=ag)], SID)
    assert [s.url for s in a.sources][1:] == [AUTO, "https://www.dupaco.com/business/cash-management"]


def test_extra_sources_capped_across_facts():
    k2 = "auto_loan|apr|model_year=2024-2027;segment=personal;term=72"
    ag = lambda v, k: [obs(v, k, url=f"https://www.dupaco.com/p{i}", page_type="product") for i in range(3)]
    a = render_facts("q", intent("list"), [rf("5.39%", agreeing=ag("5.39%", KEY)),
                                           rf("5.49%", k2, agreeing=ag("5.49%", k2))], SID)
    assert len(a.sources) == 3  # 1 winner URL + 2 extras


def test_extra_sources_dedupe_urls():
    ag = [obs("5.39%", url=AUTO, page_type="product"), obs("5.39%", url=AUTO, page_type="product", quote="again"),
          obs("5.39%", url="https://www.dupaco.com/rates")]
    a = render_facts("q", intent(), [rf("5.39%", agreeing=ag)], SID)
    assert [s.url for s in a.sources] == ["https://www.dupaco.com/rates", AUTO]


def test_extra_sources_same_snapshot_same_value_only():
    old = FactObs(KEY, "auto_loan", "apr", {}, "5.39%", "5.39%", "04_2026-08-27_rebaseline", "2026-08-27",
                  AUTO, "product", 80, None, "q", "table")
    other_value = obs("5.34%", url="https://www.dupaco.com/business/cash-management", page_type="product")
    a = render_facts("q", intent(), [rf("5.39%", agreeing=[old, other_value])], SID)
    assert [s.url for s in a.sources] == ["https://www.dupaco.com/rates"]
```

Existing tests `test_template_matches_scorer`, `test_conflict_note_not_in_text` and `test_list_one_line_per_fact_and_dedup_sources` must keep passing unchanged. They have no agreeing pages, so their source lists don't change.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compose.py -k extra_source -v`
Expected: FAIL (only the winner source is returned).

- [ ] **Step 3: Implement `_extra_sources` and call it from `render_facts`** as specified in Interfaces.

- [ ] **Step 4: Run the full unit suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. A `tests/test_system.py` test that asserts an exact `sources` list for a fact answer is the one legitimate thing that may change. Update it only if the new list is the winner followed by eligible agreeing pages, and note it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add submission/kb/compose.py tests/test_compose.py
git commit -m "feat(compose): cite up to 2 same-value pages after the winning source"
```

---

### Task 3: Verify on the public and dev evals

No product code. This task's deliverable is the comparison report. If a check fails, stop and report; don't tune around it.

**Files:**
- Create: `results/corroborating-citations-20261103/` (eval outputs, kept local like other `results/` runs)

- [ ] **Step 1: Run the public eval on a fresh store, with no judge, matching the baseline run**

```bash
export KB_DB_PATH=$PWD/results/corroborating-citations-20261103/state/kb.sqlite
mkdir -p "$(dirname "$KB_DB_PATH")"
.venv/bin/python harness/run_eval.py --submission submission --snapshots snapshots \
  --eval eval/public_eval.jsonl --conflicts-gold dev/conflicts_gold.json \
  --out results/corroborating-citations-20261103/public
```

Expected: completes with `errors: 0`.

- [ ] **Step 2: Diff the results question by question against the baseline**

Compare `results/corroborating-citations-20261103/public/per_question.jsonl` with `results/fix-verification-20261103/public/per_question.jsonl` by `id`.

Pass criteria:
- No question has lower `correctness`.
- No question has new `trap_hits`.
- No question's `answer.text` changed.
- `source_hit` goes 0 → 1 for dup-009, dup-016, dup-017 and dup-054.
- dup-064, dup-005 and dup-088 are unchanged.
- `qa_score` ≥ 0.9457 (the baseline was 0.9271).
- `conflicts` F1 and `p95_s` are not worse than the baseline report.

- [ ] **Step 3: Run the dev evals and check paraphrase and refusal behavior**

Run `KB_DB_PATH=… PY=.venv/bin/python dev/score_run.sh corroborating-citations --no-judge`, which runs public plus the paraphrase, as-of and refusal fixtures and appends to `dev/scores.csv`.

Expected:
- `heldout` is not below the most recent row in `dev/scores.csv`.
- `paraphrase_consistency` and `repeat_consistency` are not below that row.
- No refusal item in `dev/refusal_eval.jsonl` changes its answer text.

- [ ] **Step 4: Commit the scores row**

```bash
git add dev/scores.csv
git commit -m "chore(eval): record corroborating-citations scores"
```

Report back with the before/after table for the seven original misses and any other question whose `sources` changed.
