# Project Plan: "A knowledge system that knows what's true now"

Plan for the Knowledge Hackathon assignment (`Knowledge-Hackathon-Assignment.pdf`). Plan date: 2026-10-31.
I read the PDF in full (5 pages). I also looked at the kit in `hackathon-knowledge-kit-participant/`: `README.md`, `snapshots/README.md`, `harness/contract.py`, `harness/scoring.py`, `harness/run_eval.py`, `starter/solution.py` and `eval/public_eval.jsonl`. Facts from the PDF are cited by section (e.g. "PDF §5"). Facts from the kit are marked "(kit)".

---

## 1. Assignment summary

### 1.1 The problem
- A credit-union assistant answers member questions "using the credit union's own website". The site has been crawled five times, and the copies contradict each other (PDF §1).
- Sources of mess listed in the PDF (§1): a rate changes while an old page, PDF or calculator keeps the old number. Ended promotions stay online. "One page lists the **rate**, another the **APY**". Tiered tables (by balance, term or loan amount) look like conflicts if read carelessly. Old press releases and blogs describe offers, counts and hours from past years.
- Mission (PDF §1): *"reads the snapshots in date order and answers like a careful human expert would: with the **current** value, the **right tier**, a **source**, and an honest 'I don't know' when the site doesn't say."*
- Worked examples (PDF §1):
  - 60-month auto APR (2025 model year). The rate page says **5.39%**; a calculator page that was never updated still says 5.34%. *"Answering 5.34%, or both, scores zero."*
  - 10-month jumbo certificate special "as of May 2026". The answer is **3.90% APY**, not September's 4.00%. *"Questions with a date ('as of…') must be answered from the website as it stood then."*

### 1.2 What we get (PDF §2)
| Item | Notes |
|---|---|
| `snapshots_zip/` | 5 zips: `01_2026-05-12_baseline`, `02_2026-05-19_recrawl`, `03_2026-06-09_recrawl`, `04_2026-08-27_rebaseline`, `05_2026-09-28_latest`. Snapshot 05 is "current" (PDF §2). On disk (kit): 637 / 634 / 635 / 683 / 705 pages, about 32 MB unpacked. Each folder has a `manifest.json` (url, title, fetched_at) and `pages/*.md`. |
| `snapshots/README.md` | What changed between snapshots. 02 "caused the most new contradictions". 03 "Rates moved again". 04 came after a 10-week gap and added about 50 new pages. |
| `eval/public_eval.jsonl` | 40 practice questions with expected answers. |
| `starter/solution.py` | A naive baseline that scores about 0.09 (PDF §4). |
| `harness/` | The scorer the organizers use (PDF §2, §7). |

### 1.3 Required deliverables
- A folder containing `solution.py` with `build()`. `build()` returns an object with exactly three methods (PDF §3):
  - `ingest(snapshot)`: called once per snapshot, oldest first.
  - `answer(question, as_of=None)`: returns `Answer(text, sources=[Source(url, snapshot_id?, quote?)], confident, conflict_note)`.
  - `conflicts()`: returns `list[Conflict(topic, values, sources, resolution, reason)]`.
  - Shapes are defined in `harness/contract.py`. *"Don't rename anything."*
- `requirements.txt`. Installed "in a clean Python 3.11 environment" (PDF §7).
- `NOTES.md` covering *"how you decide what's current, and which API keys it needs"*, plus any paid API used (PDF §3, §7, §8).
- A GitHub repo with read access for the organizers. The solution can sit in "any folder except `starter/`" (PDF §7).
- `python harness/run_eval.py --submission <your folder> --eval eval/public_eval.jsonl` "runs cleanly from a fresh clone" (PDF §7).
- Submit the repo URL **and the commit to be graded** by the deadline (PDF §7).

### 1.4 Constraints and rules (PDF §5, §8)
- Answer only from the provided snapshots. **No live website calls.**
- Each `answer` call must finish within **60 s**. The harness enforces this with a timeout; a timeout or crash scores zero (kit).
- Loading all snapshots (the `ingest` calls) is not timed for scoring but **must finish within 30 minutes**.
- Any language model or library is allowed. A paid API must be disclosed in `NOTES.md`.
- Python ≥ 3.9 locally. Grading uses 3.11.
- The data is a real credit union's site (Dupaco). **Do not redistribute it.** This bears on whether snapshots can go in a public repo (see §9).
- Changing `harness/` or `eval/` has no effect, and hard-coding practice answers "won't help on the hidden set" (PDF §7).

### 1.5 Deadline and time budget
- PDF §7 says *"[date and time to be announced]"*. **Decided: 2 days, solo** (see §9). The timeline in §4 is a 2-day solo plan.

### 1.6 Judging criteria (PDF §5, with exact mechanics from `harness/scoring.py`)
Hidden set: *"a larger, hidden question set… includes reworded versions of the practice questions, 'as of' date questions, and questions about things that changed between snapshots."*

| Pillar | Weight | How it is computed (kit `scoring.py`) | Implication |
|---|---|---|---|
| Accuracy | 55% | Per item: share of `must_include` tokens found in `text`. Numbers are matched by value and unit, so "5.39%" equals "5.390 %". **Any `must_not_include` hit sets correctness to 0.** `should_refuse` items need `confident=False` or refusal wording **and no new numbers**. Item = 0.85·correctness + 0.15·source hit (path-level URL match) when expected sources exist. Weighted by the item's `weight` (1.5 to 2.0 for hard items). An optional Opus LLM judge (`--judge`) blends 50/50 with the token score. | Give concise answers with only the winning value(s). Never echo stale numbers, not even as "previously X". Cite the authoritative page. |
| Conflicts | 15% | 0.7·F1 against a hidden `conflicts_gold.json` + 0.3·resolution accuracy. A gold entry matches when a submitted conflict contains ≥2 gold values, or ≥1 value plus a topic keyword. | **Precision matters (F1).** Don't dump thousands of pseudo-conflicts. Put both values in `values` and the winner in `resolution`. |
| Consistency | 15% × accuracy | (a) In a paraphrase group, either every member is fully correct or all members produce the **same set of numbers**. (b) A repeat pass yields the same numbers. | Answers must be deterministic. Canonicalize paraphrases to one intent so they render the same numbers. |
| Speed | 15% × accuracy | p95 latency ≤ 2 s gives 1.0, falling linearly to 0 at 20 s. | The hot path should usually avoid synchronous LLM calls of more than about 1.5 s, or must cache aggressively. The penalty is gentle, though: each extra second of p95 above 2 s costs ≈0.6 final points at accuracy 0.75, while each question fixed out of 100 adds up to ≈0.85 (`IMPROVEMENTS.md` §1). |

Note: the public eval has no conflicts gold file, so locally the conflicts pillar is **not scored** unless we pass `--conflicts-gold`. All 40 public items are in singleton groups, so the paraphrase part of consistency is untested locally. We need our own fixtures for both (§6).

### 1.7 What the 40 practice questions tell us (kit)
Category mix: `stale_trap` 7, `tier_disambiguation` 6, `branch_info` 6, `list_completeness` 4, `entity_attribution` 4, `temporal_as_of` 4, `conflict_resolution` 3, `current_value` 3, `should_refuse` 3. Only **1 of 40** has an `as_of` date (`2026-05-31`), but the PDF says the hidden set has more. Patterns:
- **Stale traps across page types.** Mortgage rates from the current rate generation (7.13 / 7.25) against old ones (6.037%). The calculator's auto APR. Forbes "fifth" against older "fourth" press. Old `info.dupaco.com` landing-page URLs against current `dupaco.com` URLs.
- **Tiers.** Share Savings $10k–$24,999 gives 0.15. The "highest" tier is 0.20 at $25,000, not a stale 0.35. Mortgage headline should use the $100k tier. Regular against Jumbo 17-month specials.
- **Entity attribution.** Routing number 273974581 against another institution's 271176899. Bright Savings against Jump Start.
- **Refusals.** 7/1 ARM rate, construction loan rate, Mullan Avenue lobby hours.
- **Prompt noise and leakage.** `must_not_include` contains "in these facts", "coffee table", "I'm not asking again", "Which certificate term would you like". So the answer must not echo chit-chat, must not use LLM-ish phrasing about "facts", and must answer instead of asking for clarification (give the full list).
- **Non-numeric facts.** Hours ("8:30", "12:30", "noon"), addresses, URLs, closure dates ("March 31, 2026"), program details (Home$tart "15,000").

---

## 2. Proposed solution and architecture

### 2.1 Core idea
Build a **temporal, authority-ranked fact store at ingest time**. Answer from it with a **fast deterministic path**, and use an **LLM only where it pays**: extraction and judging of disputed facts offline, and a cached, constrained phrasing step plus a narrow answer check online. Every fact carries `(entity, attribute, qualifiers) → value`, plus `url`, `page_type`, `snapshot_id`, `first_seen` and `last_seen`. "Current" and "as of" are then lookups, not guesses.

### 2.2 Components and data flow
```
ingest(snapshot)                                           [offline, <=30 min total]
  1. Page normalizer   -> strip nav/footer boilerplate, split into sections/tables, content-hash
  2. Diff vs previous  -> only new/changed pages (by hash) go to extraction (most pages repeat)
  3. Page classifier   -> page_type: rate_sheet | product_page | branch_page | calculator |
                          blog/news/press | pdf/disclosure | promo | legacy_landing (info.*) | other
                          + extracted page date (e.g. "rate generation effective 2026-09-25", press date)
  4. Fact extractor    -> (a) deterministic table parser for rate tables (term/tier/balance/APR/APY/rate cols)
                          (b) regex extractors: hours, addresses, phone, routing no., URLs, counts
                          (c) LLM extractor (Claude Haiku 5.5, structured output) for prose facts on
                              high-value pages only; cached on disk by content hash
                          (d) cross-check: every distinct rate-table row is also read by Haiku 5.5;
                              agree -> accept; disagree -> conflict judge (step 9), else manual
                              review queue; cached by chunk hash and committed
  5. Canonicalizer     -> fact key = (product, attribute, qualifiers{term, tier/balance band,
                          loan-amount band, model-year range, segment personal|business,
                          metric rate|APR|APY|dividend}) ; values normalised
  6. Temporal store    -> per fact key: list of observations {value, url, page_type, snapshot,
                          taken_on, page_date}; per snapshot: set of live URLs
  7. SQLite store      -> one stdlib sqlite3 file built at ingest (not committed):
                          facts(fact_key, value, snapshot_id, url, page_type, authority, quote)
                          chunks(chunk_id, snapshot_id, url, page_type, heading_path, text)
                          chunks_fts (FTS5 over chunks.text; rank_bm25 fallback if FTS5 missing)
  8. Chroma index      -> ChromaDB collection of the same chunks with metadata
                          {snapshot_id, url, page_type}; built at ingest (not committed)
  9. Conflict judge    -> end of each ingest(snapshot): fact keys with >=2 values in this snapshot
                          where the authority rules are weak (2.3 step 6) -> Claude Sonnet 5.5,
                          temp 0, structured {same_fact | different_qualifiers, winner, reason};
                          verdicts cached by hash of the competing observations and committed
answer(question, as_of)                                    [online, target p95 < 2 s]
  1. Normalise question; strip chit-chat; resolve as_of -> latest snapshot with taken_on <= as_of;
     as_of before the first snapshot -> first snapshot; after the last -> last snapshot
     (explicit "as of May 2026" in text also parsed, e.g. to end-of-month)
  2. Intent parser     -> rules + synonym tables -> canonical intent {product, attribute, qualifiers}
                          fallback: Haiku 5.5 classifier with closed label set (cached per question)
  3. Resolver          -> facts for key valid in the chosen snapshot, ranked by authority policy (2.3)
                          -> single winner per qualifier combination; tier selection from question
  4. Hybrid retrieval  -> Haystack pipeline, filtered to the chosen snapshot_id:
     (prose questions)    SQLite FTS5 retriever (custom component) --+
                          Chroma embedding retriever ----------------+--> DocumentJoiner
                          (reciprocal_rank_fusion) -> top ~10 sections
                          -> LLM section picker (Claude Haiku 5.5, temp 0): returns the id(s) of the
                          most relevant section(s); cached by (question, snapshot)
  5. Composer          -> template answer for structured facts (deterministic); for prose questions,
                          Sonnet 5.5 at temperature 0 over the picked section(s), with a strict
                          "only these values, no stale numbers" prompt and a post-filter that strips
                          any number belonging to a known-stale value set
  6. Answer checker    -> prose answers only: Claude Haiku 5.5, temp 0, checks each value belongs to
                          the asked entity/tier and the resolved snapshot and is not stale ->
                          {pass | strip | refuse}; subtractive only (never adds numbers); cached per
                          (canonical intent, snapshot); timeout ~3 s -> send the unchecked answer;
                          kept only if it fixes more answers than it breaks (§6)
  7. Honesty gate      -> refusal trigger TBD by expert (§10): no fact found, low retrieval score,
                          or the section picker reporting "none relevant" -> confident=False,
                          "That isn't listed on the website; please contact Dupaco at ..."
                          (no digits other than ones in the question, per scorer rule)
  8. Memo cache        -> (canonical intent, snapshot) -> Answer, which gives paraphrase and repeat consistency
conflicts()
  -> precomputed at ingest end: for each fact key, every distinct value observed across all
     snapshots (any value that changed over time, plus within-snapshot disagreements)
     -> drop pairs the conflict judge marked different_qualifiers
     -> Conflict with winner (snapshot 05; the judge's verdict where the rules were weak) + reason
```

### 2.3 "What is current": written authority policy (goes into NOTES.md)
Applied in order to observations of the same fact key in the target snapshot:
1. **Exact qualifier match first.** Different term, tier, metric (rate, APR or APY), segment or model-year range means a different fact, never a conflict (PDF §6.4).
2. **Page-type authority:** official rate sheet or product rate table > product page > branch/locations page > disclosure/PDF > calculator > promo landing > blog/news/press > legacy `info.dupaco.com` landing pages.
3. **Page-internal date:** a newer "effective" date or rate generation wins over an older one.
4. **Recency of observation:** a value seen in a later snapshot beats one seen only earlier, and pages missing from the target snapshot are dead.
5. **Expiry rules:** a promo with an end date before the target date is expired. A press release is historical for counts, hours and offers unless it is the only source *and* the question is historical.
6. **Weak rules → offline conflict judge.** The rules are weak when steps 2, 3 and 5 leave a tie, or when steps 2 and 3 point to different values (a lower-authority page with a newer effective date). Then the cached verdict of the conflict judge (§2.2 ingest step 9) decides. Without a verdict (judge unavailable), answer with the higher-authority source's value. Either way, report a conflict and set `conflict_note`.

### 2.4 Tech stack and rationale
| Choice | Rationale (criterion) |
|---|---|
| Python 3.11, mostly stdlib (`re`, `json`, `sqlite3` or in-memory dicts) | Clean install in 3.11 (PDF §7). Fewer moving parts, so it runs cleanly from a fresh clone. |
| Deterministic table and regex extractors as the backbone | Most hidden questions are rates, tiers, hours and IDs, so this drives **accuracy**. Fully deterministic, which helps **consistency**. Microsecond lookups help **speed**. |
| stdlib `sqlite3`: fact table + FTS5 keyword index (see `VECTOR_DB_EVALUATION.md`) | No install. Exact lookups for tiers, `as_of`, authority and conflicts (a `GROUP BY fact_key HAVING COUNT(DISTINCT value) > 1`). FTS5 found all required values in the top 5 for 22/36 practice questions, the best single retriever tested. |
| `rank_bm25` | Kept. Keyword fallback when the grader's SQLite lacks FTS5 (probe in `build()`). |
| **ChromaDB** with its default ONNX embedding model (no torch) | Dense retrieval for reworded questions (**consistency**). Filtered by `snapshot_id` metadata for `as_of`. |
| **Haystack** (`haystack-ai`, `chroma-haystack`, `anthropic-haystack`) | Pipeline wiring: Chroma retriever + a custom FTS5 retriever component → `DocumentJoiner(join_mode="reciprocal_rank_fusion")` → LLM section picker. |
| **Claude Haiku 5.5** as the section picker over the RRF top ~10 | Picks the most relevant section, which pure ranking does poorly here (none of the retrievers alone exceeded 22/36). Haiku keeps the added latency small; temperature 0 plus a cache keeps it repeatable. |
| **Claude Haiku 5.5** (`claude-haiku-5-5`) for offline prose-fact extraction and intent fallback | Cheap and fast. Runs offline, so it stays within the 30-min ingest budget. Structured outputs keep fact keys clean (**accuracy**, **conflicts**). |
| **Claude Sonnet 5.5** (`claude-sonnet-5-5`) for online composition on prose questions only, temperature 0, cached | Best quality/latency balance for the minority of free-text answers. The cache keeps p95 low (**speed**) and identical (**consistency**). |
| **Claude Sonnet 5.5** offline conflict judge, only where the authority rules are weak (§2.3 step 6); verdicts cached and committed | Settles disagreements within a snapshot and spots look-alikes that differ by tier, term or metric. Helps **conflicts** (precision and winner) and **accuracy** (stale traps). No grading-time latency; committed verdicts keep it deterministic. (`IMPROVEMENTS.md` §4.1) |
| Two-extractor cross-check on rate-table rows: table parser + **Claude Haiku 5.5**, offline | A misread tier or term causes both wrong-tier answers and fake conflicts. Agreement → accept; disagreement → conflict judge or manual review. About 430 calls, cached and committed. (`IMPROVEMENTS.md` §4.2) |
| **Claude Haiku 5.5** online answer checker on prose answers; can only remove claims or refuse | Catches wrong-entity, wrong-tier and stale values the composer lets through (**accuracy**). Costs ≈0.6 final points per extra second of p95, so it pays if it fixes more than about 1 answer in 100. Kept only if it fixes more than it breaks. (`IMPROVEMENTS.md` §4.3) |
| Claude Opus 5.5 (`claude-opus-5-5`) offline only: build silver paraphrase sets and audit conflict candidates during development | Not in the graded path. Mirrors the scorer's optional judge model. |
| No-LLM fallback mode (env flag, auto-enabled when `ANTHROPIC_API_KEY` is absent) | Grading has a key and network (§9), so this is a safety net only: it means a failing API call degrades the answer instead of crashing. Low priority. |
| Extracted-fact store keyed by content hash, plus conflict-judge verdicts and cross-check results, **committed to the repo** (approved, §9) | Makes ingest deterministic and fast and cuts API calls during grading. Includes short `quote` excerpts (approved, §9) for `Source.quote`. The SQLite file and Chroma index are rebuilt at ingest and not committed. |

---

## 3. Data and knowledge sources: ingestion and processing
- **Only source:** the five snapshots (PDF §8). No live calls; network is used only for the Anthropic API if enabled.
- **Discovery pass (Phase 0, manual plus scripts):**
  - URL inventory per snapshot: added, removed and changed pages by hash, especially the ~50 pages new in 04.
  - Page-type histogram by URL path (`/personal/loans/…`, `/calculators/…`, `/news/…`, `/blog/…`, `info.dupaco.com/…`, `.pdf`).
  - Follow five topics across dates as the PDF suggests (§6.1): auto rates, mortgage rates, certificate specials, share-savings tiers, branch hours/closures (Hy-Vee).
- **Normalization:** remove repeated header/footer/nav blocks (detect lines appearing on more than 30% of pages). Keep markdown tables intact. Record the section heading path for each chunk (for citations and `Source.quote`).
- **Table parsing:** detect rate tables. Map column headers (Term, Minimum Balance, Dividend Rate, APY, APR, "as low as", model year) to qualifiers. Normalize terms ("60 mo", "5 years" → 60), balances ("$10,000–$24,999" → band) and metrics. Every distinct rate-table row is also read by Haiku; disagreements go to the conflict judge, or to a manual review queue (`dev/extraction_disputes.jsonl`) if the judge can't settle them.
- **Entities:** product catalog (certificates incl. specials/jumbo, share savings, checking, Bright Savings, Jump Start, auto, personal, student, mortgage, HELOC, credit cards personal/business), branches (name → address, lobby/drive-up hours, status/closure date), org facts (routing number, phone, membership eligibility, branch count, awards, URLs for join/locator/rates).
- **Temporal store:** `observations[fact_key] = [(value, url, page_type, snapshot_id, taken_on, page_date)]`. `live_urls[snapshot_id] = set`. The store is built incrementally per `ingest`, so it naturally respects "oldest first".
- **Performance budget:** about 3,300 pages total, mostly unchanged between snapshots. LLM extraction only on changed, high-value pages (target ≤ 600 calls × ~2 s with 8-way concurrency, about 3 minutes). With the committed cache this becomes 0 calls. Added offline calls: about 430 Haiku cross-check calls (one per distinct rate-table row), and Sonnet conflict-judge calls only where the rules are weak. Both are cached and committed.

---

## 4. Phased timeline
**2 days, solo** (about 20–24 focused hours). The phases run one after another. Anything not done by its slot is cut, not carried over.

| Phase | When | Goal | Milestone / exit criterion |
|---|---|---|---|
| 0. Setup and data study | Day 1 morning (2–3 h) | Repo, run the starter with `--judge` (expect about 0.09), inventory snapshots, trace 5 topics | Starter score reproduced. Authority rules draft. Page-type map. |
| 1. **Minimum working deliverable (temporal retrieval)** | Day 1 midday (3–4 h) | History-aware BM25 per snapshot, `as_of` snapshot selection, page-type authority penalty, extractive answer, refusal gate, deterministic output | **Public score ≥ 0.35** with p95 < 1 s. Runs from a fresh clone. Submittable as-is. First git tag `min-deliverable`. |
| 2. Structured fact store | Day 1 afternoon to evening (5–6 h) | Table and regex extractors, canonical fact keys, resolver plus templates for rates, tiers, hours, addresses, routing number, URLs. Commit the extracted facts. | **Public score ≥ 0.65**. Zero `stale_trap_hits` on rate questions. |
| 3. Conflicts + LLM layer | Day 2 morning to early afternoon (5–6 h) | `conflicts()` over every changed value. Haiku extraction for prose pages (committed). Sonnet composer for prose questions. Intent canonicalization plus answer memo. Offline conflict judge, rate-table cross-check, online answer checker (§2.2). | Local conflicts F1 ≥ 0.6 on our hand-built gold. Small paraphrase set consistency ≥ 0.9. **Public ≥ 0.75.** Answer checker fixes more than it breaks, or is switched off. |
| 4. Hardening and submission | Day 2 late afternoon (3–4 h) | Clean-env install test, README setup steps, NOTES.md, a short paraphrase check | All submission checks green (§8). Tag `submission` with the commit hash recorded. |
| Cut unless ahead of schedule | — | Local embeddings, promo-expiry date reasoning, `Source.quote` excerpts, "explain" CLI, the large Opus-generated paraphrase set, testing the no-key mode | Only merge if the public score does not drop. |

Note: the conflict judge, rate-table cross-check and answer checker (`IMPROVEMENTS.md` §4.1–4.3) add an estimated 4–5 h to Phase 3. Build them in that order. The answer checker is the first to cut if Day 2 runs late.

---

## 5. Task breakdown

### Phase 0: Setup and data study
- [ ] `git init` in `/Users/yuguang/Projects/interface`. Copy the kit in and put the submission folder at `submission/`. Add `snapshots_zip/` and any unpacked snapshot folders to `.gitignore`. Create the GitHub repo as **private**.
- [ ] Python 3.11 venv. Run `python harness/run_eval.py --submission starter --eval eval/public_eval.jsonl --judge` and save the baseline report. The judge is the default for every local run (§9).
- [ ] Script: per-snapshot URL diff (added/removed/changed), path histogram, duplicate boilerplate detection.
- [ ] Trace topics across 01→05: 60/84-month auto APR, 30-yr/15-yr mortgage, 10- and 17-month certificate specials (regular/jumbo), share savings tiers, Hy-Vee branch closure, lobby/drive-up hours, routing number, branch count, Forbes ranking.
- [ ] For each of the 40 public items, note which page(s) hold the right answer and which hold the trap. This becomes the error taxonomy.
- [ ] Write authority rules v1 (§2.3).

### Phase 1: Minimum working deliverable
- [ ] `submission/solution.py` with `build()` and the three methods. Import the contract the same way the starter does.
- [ ] Keep all snapshots: `pages[snapshot_id][url]`, with a `taken_on` list.
- [ ] `as_of` → snapshot resolver: latest snapshot on or before the date. A date before 2026-05-12 uses snapshot 01, and a date after 2026-09-28 uses snapshot 05. Also parse "as of <Month YYYY>" and "last year" from the question text.
- [ ] Boilerplate stripper and section chunker. Chunk dedup approach per §10.
- [ ] SQLite schema (`facts`, `chunks`, `chunks_fts`) with an FTS5 probe and `rank_bm25` fallback.
- [ ] ChromaDB collection with `snapshot_id`/`url`/`page_type` metadata. Time the embedding of all chunks; it must leave room inside the 30-min ingest limit.
- [ ] Haystack pipeline: custom FTS5 retriever component + Chroma retriever → `DocumentJoiner` with reciprocal rank fusion → Haiku section picker (structured output: chosen section ids or "none"). Cache picker results.
- [ ] URL/page-type classifier with authority weights. Down-rank calculators, news, blog, press and `info.dupaco.com`.
- [ ] Answer: best section sentence(s) trimmed to the minimal span containing the matched numbers. Always cite the URL and `snapshot_id`.
- [ ] Refusal gate with scorer-compatible wording ("isn't listed… please contact"), no digits.
- [ ] Deterministic tie-breaking (sort by score, then URL).

### Phase 2: Structured facts
- [ ] Markdown table parser plus header→qualifier mapping. Unit tests on 10 real tables (auto, mortgage, certificate, savings, credit card).
- [ ] Regex extractors: hours (with day ranges and "noon"), addresses (street + ZIP), phone, routing number (ABA checksum validation), URLs, counts ("22 branches"), dates ("March 31, 2026").
- [ ] Canonical fact key schema and synonym tables (APR/APY/rate/dividend; "CD" = certificate; "jumbo"; "model year"; personal/business).
- [ ] Resolver implementing authority policy steps 1–6. Records the losing values for conflicts.
- [ ] Intent parser: product + attribute + qualifiers from the question. Rules for "highest", "right now", "between $X and $Y", "on a 2025 vehicle".
- [ ] Templates per intent family, including list intents (all credit cards with APRs, all certificate terms, mobile wallets). No clarifying questions.
- [ ] Headline rules for under-specified questions (e.g. mortgage leads with the $100k tier, per dup-011).
- [ ] Output sanitizer: never emit a number that the resolver marked as stale for this intent.

### Phase 3: Conflicts + LLM
- [ ] Conflict builder: same fact key with ≥2 distinct values **anywhere across the snapshots**. This covers any value that changed over time (even when the old page is gone) and disagreements between live pages in the same snapshot. Emit `values` with all distinct values, `resolution` = the winner in snapshot 05, `reason` = the rule that fired or the conflict judge's reason (e.g. "updated in 03_2026-06-09", "calculator page not updated"), and the sources.
- [ ] Precision filter: drop pairs whose qualifiers don't match (a different tier, term or metric is not a conflict), and pairs the conflict judge marked `different_qualifiers`. Merge to one entry per fact key. Topic string includes the product and qualifier keywords (helps keyword matching).
- [ ] Hand-build `dev/conflicts_gold.json` (about 25 entries in the kit's format) from Phase 0 tracing. Score with `--conflicts-gold`.
- [ ] Conflict judge (§2.2 ingest step 9, §2.3 step 6): select the keys where the rules are weak. Sonnet 5.5, temperature 0, structured output `{relation: same_fact | different_qualifiers, differing_qualifier, winner, reason_code, reason}` over the competing values with their quotes, URL, page type, page date and qualifiers. Cache by hash of the competing observations plus prompt version; commit the verdicts. `different_qualifiers` → split the key, or drop it from `conflicts()` and log it to `dev/judge_review.jsonl`. Check winners against dev gold.
- [ ] Haiku extraction for prose pages (membership eligibility, Home$tart, student loans, VTO count, awards, mobile wallets), using structured output into the same fact schema. Disk cache keyed by content hash.
- [ ] Rate-table cross-check (§2.2 ingest step 4d): Haiku reads every distinct rate-table row into the fact schema, 8 calls at a time. Compare `(value, metric, qualifiers)` with the table parser. Disagreements → conflict judge; any it can't settle → `dev/extraction_disputes.jsonl` for manual review, keeping the parser's reading meanwhile. Cache by chunk hash; commit.
- [ ] Intent fallback classifier (Haiku, closed label set) when rules give low confidence. Cache it.
- [ ] Sonnet composer for prose answers: temperature 0, retrieved sections from the resolved snapshot only, instructions to avoid "facts/context" phrasing and to not ask questions. Post-filter applies.
- [ ] Answer checker (§2.2 answer step 6): Haiku 5.5, temperature 0, structured output `{verdict: pass | strip | refuse, remove_values, reason_code: wrong_entity | wrong_tier | stale | not_in_sources}`. Code applies it: `strip` removes the sentences holding those values; `refuse` goes to the honesty gate. It never adds numbers. Cache per (canonical intent, snapshot). Timeout about 3 s → send the unchecked answer. Run the public and paraphrase sets with it on and off; keep it only if fixes > breaks and p95 rises by about 1 s or less.
- [ ] Answer memo keyed by (canonical intent, snapshot). Exact-question memo for repeats.
- [ ] Latency guard: if an LLM call exceeds about 8 s, fall back to the extractive answer.

### Phase 4: Hardening and submission
- [ ] Small paraphrase set: about 40 rewordings (one per public question) plus `as_of` variants for each snapshot date, before the first and after the last. Hand-checked.
- [ ] Clean-env test: fresh clone → new 3.11 venv → `pip install -r requirements.txt` → place the snapshots as the README describes → set `ANTHROPIC_API_KEY` → run eval with `--judge`.
- [ ] Measure ingest time (must be ≪ 30 min, target < 5 min) and p95 answer latency (target < 1.5 s).
- [ ] Write `README.md` with **every setup step**: Python 3.11 venv, `pip install -r requirements.txt`, where to put the snapshot zips (they aren't in git), `export ANTHROPIC_API_KEY=...`, and the exact eval command.
- [ ] Write `NOTES.md`: approach, authority rules, as_of handling, the conflict definition and the one-entry-per-fact decision with its rationale (§9.5), API keys/paid API disclosure.
- [ ] Remove dev-only paths. Confirm the solution folder is not `starter/`.
- [ ] Tag the graded commit and record its hash.

---

## 6. Evaluation plan
| Requirement / criterion | How we check | Target |
|---|---|---|
| Accuracy (55%) | `run_eval.py --judge` on public 40. Per-category breakdown. Read every miss (PDF §4). | ≥ 0.75 public qa_score. 0 `stale_trap_hits`. |
| Generalization to the hidden set | Small paraphrase set (Phase 4) scored with the same `scoring.py` functions and `--judge`. | Held-out within 0.10 of public. |
| "As of" questions | One test per snapshot date for 6 changing facts (e.g. 10-mo jumbo: May → 3.90%, Sep → 4.00%). Also dates between snapshots, before 2026-05-12 (→ snapshot 01) and after 2026-09-28 (→ snapshot 05). | 100% on the as-of fixture. |
| Tiers / qualifiers | Fixture of tier questions (balance bands, terms, loan amounts, model years, personal vs business). | 100%. No other-tier numbers emitted. |
| Honest "I don't know" | `should_refuse` fixture (7/1 ARM, construction loan, Mullan Ave, plus 10 more invented products). Also the inverse: answerable questions must not refuse. | Refuse ≥ 95% of unanswerable. False refusals ≤ 5%. |
| Sources | Source hit rate on items with `expected_sources`. Every answer cites ≥1 URL from the resolved snapshot. | ≥ 90% hit. |
| Conflicts (15%) | `--conflicts-gold dev/conflicts_gold.json`, with gold entries covering both values changing over time and disagreements within a snapshot. Also manually review a random sample of 30 emitted conflicts for precision. | F1 ≥ 0.6 on dev gold. Sampled precision ≥ 80%. Winner correct ≥ 90%. |
| Conflict judge | Judge winners on dev-gold keys against `correct_value`. Read every `different_qualifiers` verdict by hand. | Winner agreement ≥ 90%. |
| Rate-table cross-check | Agreement rate between the table parser and Haiku. Review every dispute. | All disputes settled before submission. |
| Answer checker | Public and paraphrase sets with the checker on and off: count answers fixed and broken; compare p95. | Fixes > breaks. p95 rises ≤ ~1 s. Otherwise switched off. |
| Consistency (15%) | Paraphrase groups in our fixture (numeric-gist equality, same as the scorer). Run the full set twice and diff. | ≥ 0.95 paraphrase. 1.00 repeat. |
| Speed (15%) | p50/p95/max from the report, with and without warm caches, LLM on and off. | p95 ≤ 1.5 s. Max < 10 s. |
| Ingest ≤ 30 min | Timed full ingest in a clean env, cold cache and warm cache. | < 10 min cold, < 2 min warm. |
| Runs from a fresh clone, 3.11 | CI-style script in a fresh dir and venv. | Passes. |
| No live web calls | Grep for HTTP clients other than `anthropic`. | No other HTTP clients. |

Regression discipline: keep a `dev/scores.csv` row per run (commit, public score, held-out score, conflicts F1, p95). Do not merge a change that lowers held-out accuracy.

---

## 7. Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| Over-fitting to the 40 public items (the PDF warns hard-coding won't help) | Low hidden score | Rules are generic (page type, qualifiers, dates). Held-out paraphrase/new-topic set. No question-ID or answer-string logic. |
| Stale numbers leaking into text ("was 5.34%", LLM adds context) | Item zeroed | Composer prompt forbids history. Post-filter removes any number tagged stale for the intent. Templates show only winners. |
| Tier mis-selection or over-inclusion (listing all tiers trips `must_not_include`) | Item zeroed | Qualifier-aware resolver. Explicit headline rules. List all tiers only for list-type intents. |
| An Anthropic API call fails or is slow during grading (grading has a key and network, but calls can still fail) | Single answers fail | Committed fact store, so ingest needs few calls. Catch exceptions and fall back to the extractive answer. |
| 2-day solo schedule slips | Unfinished pillars | Phase 1 is submittable on its own. Fixed time slots: cut features rather than extend. Conflicts get at least a basic version, because they're 15%. |
| The LLM section picker adds a model call to every prose question | p95 creeps past 2 s; speed pillar drops | Haiku, short candidate texts, top ~10 only. Skip the picker for questions the fact table answers. Cache by (question, snapshot). Measure p95 with the picker on from Phase 1. |
| Chroma embedding at ingest is slow on the grader's CPU (an earlier test: ~150 s per 1,000 chunks with a different small model) | Ingest exceeds 30 min | Embed each unique chunk once across snapshots. Time it in Phase 1. If too slow, switch Chroma's embedding function to a faster model. |
| LLM latency spikes push p95 > 2 s | Speed pillar drops | Deterministic path first. Memo cache. Timeout fallback. Haiku for classification. |
| Non-determinism across paraphrases or repeats | Consistency drops | Temperature 0. Canonical intent → memoized answer. Stable sorting. |
| Conflicts list too noisy (F1 precision) or too sparse | Conflicts pillar drops | Qualifier-aware dedupe. Topic merging. Tune against dev gold. Manual precision audit. |
| The conflict judge picks the wrong winner, or calls a real conflict `different_qualifiers` | Stale answers; lost conflict recall | Rules first; the judge runs only where they're weak. Verdicts checked against dev gold. Committed verdicts can be corrected by hand. |
| The answer checker vetoes a correct answer (the item scores 0), or its verdicts vary between runs | Accuracy and consistency drop | It can only remove claims or refuse, with closed reason codes. Temperature 0 plus a cache per canonical intent. Kept only if it fixes more than it breaks. |
| The three judge additions take about 4–5 h on Day 2 | Schedule slip | Build order: conflict judge → cross-check → answer checker. The checker is the first to cut. |
| Misreading the "current" policy (e.g. a promo page still live in 05) | Wrong answers | Trace examples in Phase 0. Promo expiry parsing. Document rules in NOTES.md. |
| Snapshots accidentally committed | Rule violation | Private repo. Snapshots in `.gitignore` from the first commit. Check `git ls-files` before every push. |
| Graders can't find the snapshots, because they aren't in git | Eval fails from a fresh clone | README states the exact path to place the zips. The solution fails with a clear error message if they're missing. |
| Ingest exceeds 30 min on a slow grader (LLM extraction cold) | Disqualification or timeout | Hash-diffing. Concurrency. Committed cache. Hard time budget inside ingest that falls back to rules. |
| Scorer's numeric matching quirks (e.g. "8 a.m." text match, "$15,000" units) | Missed tokens | Use the site's own formatting in templates ("8:30 a.m.", "$15,000"). Unit-test against `scoring.contains`. |

---

## 8. Submission checklist

No demo or presentation is planned (out of scope).

Taken from PDF §7 plus kit requirements.
- [ ] **Private** GitHub repo pushed. Organizers have read access. `git ls-files` shows no snapshot data.
- [ ] `README.md` documents every setup step (venv, install, snapshot placement, `ANTHROPIC_API_KEY`, eval command). Followed once, word for word, on a fresh clone.
- [ ] `submission/solution.py` with `build()` returning an object with `ingest`, `answer`, `conflicts`. The folder is not `starter/`. Contract names unchanged.
- [ ] `requirements.txt` lists every package (`anthropic`, `haystack-ai`, `chroma-haystack`, `anthropic-haystack`, `chromadb`, `rank_bm25`), with pinned versions. Verified in a clean Python 3.11 env.
- [ ] `NOTES.md`: how it works, how "current" is decided, `as_of` handling, the conflict definition, API keys needed (`ANTHROPIC_API_KEY`), paid API disclosure (Anthropic).
- [ ] `python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl` runs cleanly from a fresh clone.
- [ ] No live website calls anywhere. Every answer is under 60 s. Ingest is under 30 min.
- [ ] Snapshot data not in the repo. Committed extracted facts contain no page text.
- [ ] Repo URL and graded commit hash sent before the end of day 2.

---

## 9. Decisions (answered 2026-10-31)
1. **Deadline:** 2 days. **Solo.** The timeline in §4 is a 2-day solo plan.
2. **API at grading:** assume `ANTHROPIC_API_KEY` and network access are available. Document every setup step in `README.md`.
3. **Committed facts:** yes, commit the extracted facts, including short `quote` excerpts from the site.
4. **Repo:** private. Snapshots stay out of git.
5. **Conflicts:** any value that changed over time counts. **Start with one entry per fact key**: one `Conflict` listing every distinct value the fact had across the snapshots, with `resolution` = the snapshot-05 value.
   - *Example (illustrative numbers):* 10-month jumbo APY is 3.90% in 01–02, 3.95% in 03, and 4.00% in 04–05. Per fact: one entry `[3.90%, 3.95%, 4.00%]` → 4.00%. Per change (rejected for now): two entries, `3.90% → 3.95%` and `3.95% → 4.00%`.
   - *Why:* `score_conflicts` in `harness/scoring.py` matches each submitted conflict to at most one gold entry, and a gold entry is a topic plus a list of values. One entry per fact matches that shape and states the current winner clearly.
   - *Trade-off:* if the hidden gold has one entry per change, this loses recall on facts that changed more than once. If we switched to per change and the gold is per fact, the extra entries would lower precision. Neither choice hedges the other.
   - *Revisit if:* organizers clarify the gold format, or our dev gold (`dev/conflicts_gold.json`) suggests otherwise. Switching is a small change in the conflict builder.
   - This decision and its rationale also go into `NOTES.md`.
6. **`as_of`:** before the first snapshot → snapshot 01. After the last → snapshot 05. Between snapshots → the latest snapshot on or before the date (our assumption; not stated explicitly).
7. **Judge:** use the scorer's existing `--judge` for local evaluation.
8. **Snapshots at grading:** the same five.
9. **Retrieval:** Haystack pipeline combining SQLite FTS5 and ChromaDB with reciprocal rank fusion, then an LLM call picks the most relevant section. `rank_bm25` is kept.

Still open (small): how to read date phrases inside the question when `as_of` is null ("last year", "in May"). Default: parse explicit month/year phrases; otherwise use snapshot 05.

---

## 10. TODO: decisions for an expert
- [ ] **Chunk dedup.** 93–97% of pages change between consecutive snapshots (rotating widgets, reCAPTCHA blocks, inventory tables), so page-hash diffing saves almost nothing. Option on the table: hash cleaned text sections instead (27,473 chunks → 11,777 unique). Decide the dedup unit and how it interacts with per-snapshot filtering in SQLite and Chroma. Evidence: `VECTOR_DB_EVALUATION.md`. **Recommendation (awaiting decision):** `IMPROVEMENTS.md` §2.
- [ ] **Refusal trigger.** Decide when `answer` refuses: (a) no matching fact in the fact table, (b) low retrieval score, (c) the LLM section picker returns "none relevant", or a combination. Evidence: "construction loan" appears on 27–40 pages per snapshot, and refusal questions got higher best-match scores (0.79, 0.77) than some answerable ones (0.77, 0.69), so (b) alone is unreliable. **Recommendation (awaiting decision):** `IMPROVEMENTS.md` §3.
