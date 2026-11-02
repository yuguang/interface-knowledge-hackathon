# Improvements: agentic design patterns applied to the plan

Date: 2026-10-31. Scope: the two open decisions in `PROJECT_PLAN.md` §10 (chunk dedup, refusal trigger), plus other patterns that raise accuracy and the conflicts score.

Inputs:
- The pattern docs in `~/Projects/agentic-design-patterns-docs/` (`pattern-discussion/*.md`, `mermaid-diagrams/*.mmd`).
- `PROJECT_PLAN.md`, `VECTOR_DB_EVALUATION.md`, `harness/scoring.py`.
- The `should_refuse`, `conflict_resolution` and `temporal_as_of` items in `eval/public_eval.jsonl`.
- Three text searches in the snapshots (Mullan Avenue, 7/1 ARM, construction-loan rates). The snapshot data was not otherwise inspected.

---

## 0. Summary

| # | Suggestion | Patterns | Helps | Status |
|---|---|---|---|---|
| A | Chunk dedup: store each chunk once, record which snapshots it appears in | Memory Management, Resource-Aware Optimization, Knowledge Retrieval (RAG) | Ingest time (30-min limit) | Recommended. Awaiting decision (`PROJECT_PLAN.md` §10) |
| B | Refusal trigger: layered evidence check | Routing, Reflection, RAG, Guardrails, Exception Handling, Evaluation & Monitoring | Accuracy: `should_refuse` items and false refusals | Recommended. Awaiting decision (§10) |
| 1 | Offline conflict judge | Reflection, Reasoning Techniques | Conflicts (precision, winner). Accuracy (stale traps) | **In `PROJECT_PLAN.md`** |
| 2 | Two extractors that check each other | Reasoning Techniques, Parallelization, Human-in-the-Loop | Accuracy (tiers). Conflicts (precision) | **In `PROJECT_PLAN.md`** |
| 3 | Online answer checker for prose answers | Guardrails, Reflection, Exception Handling | Accuracy | **In `PROJECT_PLAN.md`**, kept only if it fixes more answers than it breaks |
| 4 | Judge for development misses | Evaluation & Monitoring, Learning & Adaptation, Human-in-the-Loop | Faster fixes. Dev conflict gold | Proposed |
| 5 | Narrow route for "what changed" questions | Routing, Memory Management | Accuracy on history questions, if the hidden set has them | Proposed. Low priority (§4.5) |

---

## 1. Findings that shape the suggestions

1. **How refusals are scored.** In `score_item`, a `should_refuse` item gets credit only if `confident=False` or the text matches `REFUSAL_RE`, **and** the text has no numbers beyond those in the question.
   - A phone number in "please contact us" zeroes the item. The plan's current honesty-gate text ("please contact Dupaco at ...") would do this if the blank were filled with a phone number.
   - For every other category `confident` is not read, so setting it honestly costs nothing.
   - `REFUSAL_RE` accepts, among others, "isn't listed", "please contact", "no longer offered" and "I don't know".
2. **All three public refusals name something the site mentions, without the value asked for.**
   - Mullan Avenue appears in a branch list ("Waterloo: W Mullan Ave.") and in blog posts, with no hours.
   - The 7/1 ARM appears in a blog list ("Dupaco's ARMs: 30-year 5/1, 7/1, 10/1 ARM"), with no rate.
   - Construction loans are described on many pages, and no rate appears next to them in any snapshot.
   - So an "unknown product" check misses all three, and retrieval ranks them high (`VECTOR_DB_EVALUATION.md` P8). The real trap is borrowing a value: answering Mullan Avenue with the general 8:30 lobby hours adds numbers and scores zero.
3. **Dedup does not change answer quality.**
   - Retrieval is filtered to one resolved snapshot, so copies of a chunk from other snapshots never compete.
   - What dedup buys is less ingest work. 27,473 chunk postings collapse to 11,777 distinct chunks (about 2.3×). That ratio applies to Chroma's CPU embedding and to Haiku extraction.
   - Embedding speed is the main ingest risk. `VECTOR_DB_EVALUATION.md` §4.2 measured about 150 s per 1,000 chunks for a small ONNX model on the dev Mac.
4. **The speed penalty is gentle.** From `summarize()`, final = 0.55·A + 0.15·Conflicts + 0.15·Consistency·A + 0.15·Speed·A. Speed is 1.0 at p95 ≤ 2 s and falls linearly to 0 at 20 s.
   - Each extra second of p95 above 2 s costs 0.15·A/18, or about **0.6 points** of final score at A = 0.75.
   - Each question fixed out of 100 (equal weights) adds up to 0.55 + 0.15 + 0.15 = **0.85 points**.
   - So a synchronous LLM call of about 1 s pays for itself if it fixes more than about 1 answer in 100. The real costs of an online LLM step are breaking correct answers and run-to-run variation, which hits the consistency pillar.
   - p95 ignores the slowest 5% of questions, so a slow path taken by fewer than 5% of questions doesn't affect the speed score.
5. **Change-related questions want one point in time, never the history.**
   - All seven public `conflict_resolution` / `temporal_as_of` items ask for the value now or as of a date.
   - dup-006 (`as_of` May 2026) lists `4.00%` in `must_not_include`, although its reference answer says "it later rose to 4.00%". Mentioning the other-time value zeroes the item.

---

## 2. Chunk dedup (`PROJECT_PLAN.md` §10)

### Options
1. **Store each chunk once and record which snapshots it appears in. Recommended.**
2. **Keep one row per snapshot and use the hash only to skip repeat work.** That gives 27.5k rows with a plain `snapshot_id`, and each distinct text is embedded and extracted once. Filtering is simpler, but the index is 2.3× bigger for no other gain. Use it as the fallback if the membership flags cause trouble in Haystack.
3. **Hash the text alone, ignoring the URL. Rejected.** Identical text on a rate page and a calculator would become one row, which breaks page-type authority and citations.

### Design of option 1
- **Dedup key:** `sha1(url_key + heading_path + cleaned_text)`. Including the URL keeps each chunk tied to one page, so authority and citations stay per page. Dedup happens across time only.
- **Chunking (RAG: "keep related parts together"):**
  - Split on headings first, then pack paragraphs up to about 1,500 characters.
  - Never split a table, and keep the header row with every table chunk.
  - This way a rotating widget or a new paragraph changes only its own chunk, not every chunk after it.
- **SQLite:**
  - `chunk(chunk_id, chunk_hash UNIQUE, url, heading_path, text)` and `chunk_snapshot(chunk_id, snapshot_id, page_id)`, as in `VECTOR_DB_EVALUATION.md` §6.
  - FTS5 gets an *indexed* `snaps` column holding tokens such as `s01 s02 s05`. The snapshot filter then becomes part of the match, and that column's BM25 weight is set to 0:
    ```sql
    CREATE VIRTUAL TABLE chunk_fts USING fts5(text, snaps,
      tokenize="porter unicode61 tokenchars '$%.-/'");
    -- :m = 'snaps:s03 AND (' || <cleaned query terms> || ')'
    SELECT rowid, bm25(chunk_fts, 1.0, 0.0) AS score
    FROM chunk_fts WHERE chunk_fts MATCH :m
    ORDER BY score, rowid LIMIT 50;
    ```
  - This query shape is not measured yet. The measured alternative is a `snaps UNINDEXED` column with a post-filter (41 ms p95).
  - When a later snapshot sees a chunk again, its `snaps` value is rewritten (FTS5 update by rowid).
- **Chroma:**
  - One document per distinct chunk, with id = `chunk_id` and metadata `{url, page_type, in_s01 … in_s05}`. The `in_sNN` fields are true/false, which every Chroma version supports.
  - Filter with `where={"in_s03": True}`. In Haystack: `{"field": "meta.in_s03", "operator": "==", "value": True}`.
  - When a later snapshot sees a chunk again, update only its flags (Chroma `collection.update(ids=…, metadatas=…)`). Don't re-embed it. Check that writing through Haystack's document store doesn't re-embed on overwrite.
- **Facts stay per snapshot.** Extraction (table, regex, Haiku) runs once per chunk hash and is cached. Every extracted fact is still written to `fact_obs` once for **each** snapshot the chunk appears in, so history, `as_of` and conflicts are unaffected.
- **Per-snapshot page attributes** (`page_date`, live or dead) stay in the `page` table, reached through `chunk_snapshot.page_id`. They are never stored on the deduplicated chunk.
- **Patterns:**
  - Memory Management: index once, add metadata, filter by time range.
  - Resource-Aware Optimization: cache expensive results.
  - Knowledge Retrieval (RAG): split at natural breaks.

---

## 3. Refusal trigger (`PROJECT_PLAN.md` §10)

### Options
1. **Layered evidence check. Recommended.**
2. **Refuse whenever the fact table has nothing.** Fast and deterministic, but the fact table is incomplete by design. It would refuse answerable prose questions the extractors missed, such as student loans (dup-101) and Hy-Vee (dup-076).
3. **Let the Sonnet composer decide.** Simplest, but it adds a slow call to every question, and it is the option most likely to borrow a nearby value.

A retrieval-score threshold alone is already ruled out: the measured best-match scores for refusal questions were higher than for some answerable ones (§10 evidence).

### Design of option 1 (runs in this order)
1. **Fact table first (Routing).** If the intent parser yields a structured intent and the resolver has a fact for the exact key in the resolved snapshot, answer from the template. A missing fact does **not** trigger a refusal. The question falls through to step 2, along with the requested slot (entity, attribute, qualifiers).
2. **Ask "is the value there?", not "is this relevant?" (Reflection; RAG's "check sources are good").** For the exact entity and attribute asked, the Haiku section picker returns one of:
   - `answered`: a section states the value for that entity. Compose the answer.
   - `topic_only`: sections discuss the entity but give no value. Give a partial refusal.
   - `none`: give a plain refusal.
   
   The picker uses structured output at temperature 0 and is cached by (canonical intent, snapshot). This replaces its current output ("most relevant section ids, or none"), so it adds no extra call.
3. **Number check (Guardrails, deterministic code).**
   - Every number in the draft must appear in the cited facts or sections of the resolved snapshot, attached to the entity named in the question.
   - A sentence with a number that fails the check is removed. If the requested value is gone, the answer becomes a refusal.
   - A named branch or product never borrows a general value or another entity's value. For example, Mullan Avenue must not get the general 8:30 hours.
4. **Online answer checker** (suggestion 3, §4.3) runs next, on prose answers only.
5. **How refusals read.** All of them use `confident=False`.
   - `topic_only`: one short sentence on what the site does say about the entity, then "The rate isn't listed on the website; please contact Dupaco." No digits.
   - Present in older snapshots but not the resolved one: "… is no longer offered", which `REFUSAL_RE` also matches. No digits.
   - `none`: "That isn't listed on the website; please contact Dupaco."
   - Never include a phone number, an address, or any digit that isn't in the question.
6. **Retrieval score is only a shortcut (Resource-Aware Optimization).** If neither FTS5 nor Chroma returns anything that mentions the entity, skip the picker and refuse. Otherwise it never refuses on a threshold.
7. **Failures (Exception Handling).** If the picker errors or times out, fall back to the extractive answer, which still passes the number check in step 3. This matches the plan's existing fallback.

### Calibration (Evaluation & Monitoring)
- Use the refusal fixture from `PROJECT_PLAN.md` §6, plus "named entity, missing value" cases, such as another branch's unpublished attribute or a product variant listed without a rate. Find these during Phase 0 tracing.
- Check for false refusals on the 37 answerable public questions and on the paraphrase set.
- Targets are unchanged from §6: refuse at least 95% of unanswerable questions, with false refusals at most 5%.

---

## 4. Judges and other patterns

**Where a judge should sit:** offline wherever possible (at ingest or during development, cached and committed). That way it adds no latency and gives the same verdict on every run. Use an online judge only where the question can't be known in advance, which means prose answers, and even then only a checker that can remove things, never add them. The score arithmetic is in §1.4.

### 4.1 Offline conflict judge (in the plan)
- **When it runs:** at the end of each `ingest(snapshot)`, for each fact key with two or more distinct values in that snapshot. Apply the authority rules (`PROJECT_PLAN.md` §2.3) first. Call the judge only when the rules are weak:
  - steps 2, 3 and 5 of §2.3 leave a tie; or
  - steps 2 and 3 point to different values, e.g. a lower-authority page with a newer effective date.
- **The judge:** Claude Sonnet 5.5 at temperature 0, with structured output `{relation: same_fact | different_qualifiers, differing_qualifier, winner, reason_code, reason}`.
  - `reason_code` is one of `newer_effective_date`, `higher_authority_page`, `expired_promo`, `historical_page`, `other`.
  - Input: the competing values with their short quotes, URL, page type, page date, qualifiers and snapshot.
- **What happens with the verdict:**
  - `same_fact`: the resolver uses the winner for that snapshot, and the conflict's `reason` cites the judge's reason.
  - `different_qualifiers`: it is not a conflict. Split the key by `differing_qualifier` when the judge gives one. Otherwise drop it from `conflicts()` and log it to `dev/judge_review.jsonl` so the canonicalizer can be fixed.
- **Cache:** keyed by a hash of the sorted competing observations plus the prompt version, and committed next to the extracted-fact cache. At grading the judge is only called for observation sets it hasn't seen.
- **Fit with `PROJECT_PLAN.md` §9.5 (one conflict entry per fact key):** changes over time need no judge, because the later snapshot wins and `resolution` is the snapshot-05 value. The judge only settles disagreements within one snapshot, including any in snapshot 05, which is where `resolution` comes from.
- **Checks:** winners agree with `dev/conflicts_gold.json` at least 90% of the time. Read every `different_qualifiers` verdict by hand.
- **Patterns:** Reflection (a critic with a rubric); Reasoning Techniques ("argue both sides → grade → pick a winner").

### 4.2 Two extractors that check each other (in the plan)
- **What:** run both the deterministic table parser and Haiku 5.5 on each distinct rate-table row (about 433 across all snapshots, `VECTOR_DB_EVALUATION.md` §2). Haiku uses structured output into the same fact schema. Compare `(value, metric, qualifiers)`:
  - They agree: accept the fact.
  - They disagree: send both readings to the conflict judge (4.1). If the judge can't settle it, write it to `dev/extraction_disputes.jsonl` for manual review during development (Human-in-the-Loop). Keep the parser's reading until it's reviewed.
- **Why:** a misread tier, term or metric is the most likely source of both wrong-tier answers (zeroed by `must_not_include`) and fake conflicts (lower F1 precision).
- **Cost:** about 433 offline Haiku calls, run 8 at a time (Parallelization), cached by chunk hash and committed. No calls at grading.
- **Checks:** report the agreement rate, and settle every dispute before submission.
- **Optional extension:** cross-check the regex extractors for hours and addresses the same way.

### 4.3 Online answer checker for prose answers (in the plan, conditional)
- **What:** on prose answers only, after the composer and the deterministic number check (§3 step 3), Haiku 5.5 at temperature 0 checks each value in the draft. Does it belong to the asked entity and tier, and to the resolved snapshot? Is it outside the intent's stale-value set?
- **Output:** `{verdict: pass | strip | refuse, remove_values, reason_code: wrong_entity | wrong_tier | stale | not_in_sources}`. Code applies the verdict:
  - `strip` removes the sentences that hold those values.
  - `refuse` renders a refusal (§3 step 5).
- **It can only take things out.** It can remove claims or turn the answer into a refusal. It never adds or rewrites numbers, so it can't introduce a made-up value.
- **Consistency:** verdicts are cached per (canonical intent, snapshot), so paraphrases and repeats get the same verdict.
- **Latency:** a budget of about 1 s (estimate). After a timeout of about 3 s, send the unchecked answer (Exception Handling). Each added second of p95 costs about 0.6 points (§1.4).
- **Keep rule:** run the public set and the paraphrase set with the checker on and off. Keep it only if it fixes more answers than it breaks and p95 rises by about 1 s or less. Otherwise switch it off with a flag.
- **Patterns:** Guardrails (an output check); Reflection (a critic, one pass, no revision loop).

### 4.4 Judge for development misses (proposed)
- **What:** after each local run, Opus 5.5 labels every miss with one cause: stale value, wrong tier, wrong entity, false refusal, missed refusal, missing list item, chit-chat leak, or clarifying question. It proposes the smallest fix: a synonym, rule, template or authority weight.
- **Keep rule:** keep a fix only if the held-out score doesn't drop (`dev/scores.csv`); otherwise roll it back. This is Learning & Adaptation's "test, then deploy or roll back".
- **Conflict gold:** Opus also drafts candidate gold conflicts from the fact history, and you confirm them into `dev/conflicts_gold.json` (Human-in-the-Loop). That's faster than writing about 25 entries by hand.
- Development only. Nothing in the graded path.

### 4.5 Narrow route for "what changed" questions (proposed, low priority)
- PDF §5 says the hidden set includes "questions about things that changed between snapshots". So far the evidence (§1.5) says these ask for the value at one point in time and penalize the other-time value. The plan's no-stale-values rule already handles that.
- **What:** answer from the fact history (`fact_resolved` ordered by `taken_on`), as old → new with dates, only when the question explicitly asks about history ("what was it before", "when did it change", "used to"). Every other question keeps the no-stale-values rule.
- **Risk:** a loose detector would let old values into ordinary questions and zero them. Build it only if the hidden-set wording or new practice items show explicit history questions.

### 4.6 Considered and not worth it here
- **Several judges debating or voting online:** slow, and the verdicts vary between runs (consistency pillar).
- **Chatting multi-agent setups (Multi-Agent Collaboration, agent-to-agent messaging):** a fixed pipeline of specialist steps does the same job with less coordination.
- **Planning, Model Context Protocol, Goal Setting, Exploration & Discovery:** no fit beyond the Phase 0 data study.
- **Human review at grading time:** no human is in the loop then.
- **Opus as the online checker (to mirror the grader's judge):** several seconds per call. Haiku is enough for a narrow check that only removes things.

---

## 5. Where each model runs after these changes

| Model | Online (graded path) | Offline (ingest, cached and committed) | Development only |
|---|---|---|---|
| Claude Haiku 5.5 | Intent fallback. Section picker (three-way verdict if refusal option 1 is adopted). Answer checker. | Prose-fact extraction. Rate-table cross-check. | — |
| Claude Sonnet 5.5 | Prose composer | Conflict judge | — |
| Claude Opus 5.5 | — | — | Paraphrase sets, miss labelling, draft gold conflicts, conflict audit |

---

## 6. Open decisions
1. **Chunk dedup:** option 1 (recommended) or option 2. See §2.
2. **Refusal trigger:** option 1 (recommended), 2 or 3. See §3.
3. **Suggestions 4 and 5:** whether to adopt them. See §4.4 and §4.5.
4. **Schedule:** suggestions 1–3 add an estimated 4–5 h to Day 2 (`PROJECT_PLAN.md` §4). The proposed build order is conflict judge → cross-check → answer checker, and the checker is the first to cut.
