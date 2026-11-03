# NOTES: how the system decides what is current

## Overview

The site is crawled five times and the copies disagree, so the system does not search the pages and hope
for the best. At ingest it reads each snapshot, oldest first, and turns the pages into **facts**. A fact
is a normalised value (for example `5.39%`) filed under a key made of a product, an attribute and the
qualifiers that make it specific: term, balance tier, loan amount, model year, personal or business, branch
name. A 60-month rate and a 48-month rate, or the rate and the APY for the same account, get different
keys, so they are never treated as a conflict. Each fact remembers its page, its page type, the page's own
date if it has one, and the snapshot it came from. Tables are parsed by code, hours, addresses and the
routing number by regular expressions, and prose facts by Claude Haiku 5.5. The prose facts are cached by
chunk hash and the cache is committed, so a fresh clone makes few API calls.

**How it decides what is current.** For one fact key in one snapshot, only pages that exist in that
snapshot count. News and press pages, and promotions whose stated end date has passed, are set aside when
any other source exists. The remaining observations are narrowed in order: the page type with the highest
authority wins (rate sheet, then product page, branch page, disclosure, calculator, promotion, blog or
news, and last the old `info.dupaco.com` pages); then the newest date printed on the page; then the value
that first appeared in the latest snapshot. This is how a rate page's 5.39% beats a calculator page that
was never updated and still says 5.34%. Anything still tied is settled by page type and then URL, so the
result is always the same. The winner is stored per snapshot, and the losing values are kept for the
conflict report. "Current" is the winner in the latest snapshot (05). A question with an `as_of` date is
answered from the latest snapshot crawled on or before that date, and nothing else is consulted, so it can't
see later values. The full rules are in the policy section below.

**Answering.** The question is reduced to an intent (product, attribute, qualifiers), with rules first and
Claude Haiku 5.5 only when the rules find no product, so reworded questions land on the same intent. If a
resolved fact matches, a template writes the answer from the winning value and cites its page. Only the
winning value is stated, because repeating an old number scores as an error. When no fact matches, the
system searches the chosen snapshot with keyword search and embedding search merged together. Haiku picks the
sections that state the asked value for the asked product, and Claude Sonnet 5.5 writes a short answer from
them alone. If no section states the value, the system says it isn't listed on the website and points the
member to Dupaco, with no numbers in the reply. This also covers things the site mentions but never prices,
such as a 7/1 ARM rate or a construction loan rate. A final pass removes any value known to be stale for the
asked fact. Answers are memoised per canonical intent and snapshot, which keeps repeated and reworded
questions consistent.

**Conflicts.** `conflicts()` returns one entry per fact key whose value differs across the snapshots, whether
it changed over time or two pages disagreed within a snapshot. Each entry lists every value in the order it
appeared, and `resolution` is the current value. Filters drop look-alikes that are really different tiers,
terms or table rows. Where the rules can't separate two values, the system picks the higher-authority page
and reports the disagreement. There is no separate model that judges these ties; that idea is in
`IMPROVEMENTS.md` but was not built.

The sections below give the details: the step lists, the exact authority policy and how the code applies it,
the `as_of` rule, the refusal rule, the choices for chunk dedup and conflicts, API use, and local scores.

## Approach

`solution.py` → `build()` returns `kb.system.KnowledgeSystem` with the three contract methods. Every
stage fails soft: an exception in one page or one stage is logged and skipped, and `answer()` falls back
to the refusal text instead of raising.

**Ingest** (`ingest(snapshot)`, oldest snapshot first):
1. Learn the snapshot's boilerplate lines (nav, footers) and strip them. Split each page into chunks.
2. Classify each page by URL and title into a page type with an authority score (see below). Read a
   page-internal date when there is one (an "effective" date, a press-release date).
3. Extract facts. Each fact has a key `product / attribute / qualifiers` (term, tier, loan amount, model
   year, segment, branch name, ...) and a normalised value:
   - a table parser for rate tables (term × tier × metric);
   - regex extractors for branch hours, addresses, phones, routing number, counts, awards, closures;
   - Claude Haiku 5.5 for prose facts on in-scope chunks (eligibility, programs, awards, mobile
     wallets, ...). Lookups go to `submission/data/facts_cache.jsonl` first, then to the API. The LLM
     time is capped at 300 s per snapshot; once the cap is spent, remaining uncached chunks are skipped.
     Answers are cached in `submission/.cache/llm.sqlite`. The committed `facts_cache.jsonl` holds 231 chunks (940 prose facts, built with
     `dev/build_facts_cache.py`), keyed by chunk hash, so a fresh clone makes no Haiku calls for chunks
     it covers. Only chunks missing from it (e.g. if the cleaning or chunking code changes) go to the API,
     up to the 300 s/snapshot cap.
4. Resolve every fact key in the snapshot with the authority policy below, and store the winner per
   snapshot.
5. Index the snapshot's chunks for keyword search (SQLite FTS5; `rank_bm25` when the SQLite build lacks
   FTS5) and dense search (ChromaDB with its default ONNX all-MiniLM-L6-v2 model, no torch).

**Answer** (`answer(question, as_of)`):
1. Pick the snapshot from `as_of` (rule below).
2. Parse the question into an intent (product, attribute, qualifiers) with rules first, and Claude Haiku
   5.5 when the rules find no product. Paraphrases map to the same canonical intent, and the answer is
   memoised per (canonical intent, snapshot). This makes reworded and repeated questions give the same
   numbers.
3. Look for resolved facts in that snapshot that match the intent. For a stated amount, the narrowest
   band containing it matches. Headline rules cover under-specified questions: a mortgage rate means the
   $100k tier and the 30- and 15-year terms, and an auto loan rate means the newest model years. When
   facts match, a template renders the answer from them and cites the winning page.
4. Otherwise, retrieve: FTS5 and Chroma top 30 each, fused by reciprocal rank fusion in a Haystack
   pipeline. Claude Haiku 5.5 then picks up to 2 relevant sections, or none. Claude Sonnet 5.5 writes a
   short answer from those sections only. It may reply NONE, which turns into a refusal.
5. A sanitiser removes sentences that state a value known to be stale for the asked fact. It also removes
   banned phrasing (chit-chat echoes, clarifying questions).

**Conflicts** (`conflicts()`): built once from all observations across all snapshots (see the
one-conflict-per-fact decision below).

## Authority policy: what is current

Applied in order to observations of the same fact key in the target snapshot (PROJECT_PLAN §2.3):

1. **Exact qualifier match first.** Different term, tier, metric (rate, APR or APY), segment or model-year range means a different fact, never a conflict (PDF §6.4).
2. **Page-type authority:** official rate sheet or product rate table > product page > branch/locations page > disclosure/PDF > calculator > promo landing > blog/news/press > legacy `info.dupaco.com` landing pages.
3. **Page-internal date:** a newer "effective" date or rate generation wins over an older one.
4. **Recency of observation:** a value seen in a later snapshot beats one seen only earlier, and pages missing from the target snapshot are dead.
5. **Expiry rules:** a promo with an end date before the target date is expired. A press release is historical for counts, hours and offers unless it is the only source *and* the question is historical.
6. **Weak rules → offline conflict judge.** The rules are weak when steps 2, 3 and 5 leave a tie, or when steps 2 and 3 point to different values (a lower-authority page with a newer effective date). Then the cached verdict of the conflict judge (§2.2 ingest step 9) decides. Without a verdict (judge unavailable), answer with the higher-authority source's value. Either way, report a conflict and set `conflict_note`.

How the code (`kb/resolve.py`, `kb/model.py`) applies it:
- Step 1 is built into the fact key: the qualifiers are part of the key, so different tiers or terms
  never compete.
- Only pages present in the target snapshot take part (step 4, "pages missing are dead").
- Step 5 is applied first. News/press observations are dropped when a non-news source for the same
  key exists. A promo is dropped when its quote states an end date ("ends", "through", "until", ...)
  before the snapshot date.
- Steps 2, 3 and 4 then narrow the candidates in that order. The authority scores are rate sheet 100,
  product 80, branch 70, disclosure 60, other 40, calculator 30, promo 25, blog 15, news 10,
  legacy 5. Next comes the newest page date. Last comes the value that first appeared in the latest
  snapshot. The rule that settled the disagreement is recorded as the conflict `reason`.
- **Step 6 is not implemented: there is no offline conflict judge.** If steps 2–5 still leave a tie,
  the higher-authority value wins. Among equal candidates, the page with the smallest URL wins, so the
  result is deterministic.

## `as_of` rule

- No `as_of` → snapshot 05 (2026-09-28), the current site.
- Otherwise use the latest snapshot whose crawl date is on or before `as_of`. A date before the first
  crawl (2026-05-12) maps to snapshot 01. A date after the last crawl maps to snapshot 05.
- With no `as_of` argument, a date in the question counts only when anchored: "as of May 2026", "as it
  stood on …", "back in June 2026", or "in May 2026" when no event verb precedes it. A month alone
  means the month's last day. An unanchored date ("closing March 31, 2026") is an event date, not an
  `as_of`. An explicit `as_of` argument always beats a date in the question.
- Every lookup (facts, retrieval, memo) is scoped to the chosen snapshot, so an "as of" answer cites
  that snapshot's pages.

## Refusal rule

Refuse when there is **no selected fact** for the intent **and** the section picker returns nothing.
Retrieval score alone never triggers or blocks a refusal. Two more cases also refuse: a product named
in the question appears in none of the retrieved sections, and Sonnet answers NONE.

The refusal text is fixed: "That isn't listed on the website, so I can't confirm it. Please contact
Dupaco directly for this information." It has `confident=False` and contains no digits. Without an LLM,
the picker keeps the top hit only when every retriever that returned results ranked it first.
Otherwise it returns nothing, and the system refuses.

## Chunk dedup decision

The dedup unit is the cleaned chunk. A chunk's identity is `sha256(url + "\n" + cleaned text)`, so
the same text on two different pages makes two chunks. Each distinct chunk gets one `chunk` row.
Membership per snapshot lives in `chunk_snapshot`. FTS5 and Chroma index each distinct chunk once.
Queries filter by snapshot membership at query time (Chroma: a per-snapshot metadata flag, then exact
cosine over that snapshot's vectors). A chunk that recurs in a later snapshot is not embedded again. Cached
LLM prose facts are keyed by the chunk hash.

## Conflicts: one entry per fact (PROJECT_PLAN §9.5)

**Decision.** Any value that changed over time counts. The system emits one `Conflict` per fact key,
listing every distinct value the fact had across the snapshots in first-seen order. `resolution` is
the current value: the fact as resolved in snapshot 05, or in the latest snapshot that still has the
fact. `reason` names the rule that decided it (for example `authority:product over calculator`,
`updated in 04_2026-08-27_rebaseline`, `most recent year (2025)`).

**Example (illustrative numbers):** 10-month jumbo APY is 3.90% in 01–02, 3.95% in 03, and 4.00% in
04–05. The system emits one entry `[3.90%, 3.95%, 4.00%]` → 4.00%, not two per-change entries.

**Why.** `score_conflicts` in `harness/scoring.py` matches each submitted conflict to at most one
gold entry. A gold entry is a topic plus a list of values. One entry per fact matches that shape and
states the current winner clearly.

**Trade-off.** If the hidden gold has one entry per change, this loses recall on facts that changed
more than once. If we switched to one entry per change and the gold is per fact, the extra entries
would lower precision. Neither choice hedges the other. Switching is a small change in the conflict
builder.

Precision filters decide only *whether* a fact is emitted. An emitted conflict still lists every
value. The filters:
- Values seen together on one page are table rows, not a disagreement.
- List-valued attributes never conflict.
- Prose values are compared by the figures they state (years are ignored).
- Keys that report one fact over time (yearly keys, relabelled model-year buckets) merge into one.
- One change shown both as APY and as rate, or under mirrored labels (business/personal,
  IRA/certificate), is reported once.

On our dev gold (`dev/conflicts_gold.json`, 22 entries), the build emits 29 conflicts: F1 0.784,
resolution accuracy 0.909.

## API keys, paid APIs, and the no-LLM fallback

- **`ANTHROPIC_API_KEY` is required** for full quality. The only network client in `submission/` is
  the `anthropic` SDK, which calls the Anthropic API. Chroma's `DefaultEmbeddingFunction`
  (`submission/kb/retrieval/dense.py`) also downloads its ONNX embedding model (all-MiniLM-L6-v2) on
  first use, into `~/.cache/chroma/`. That is a one-time model download, not a website or API call.
  Answers come only from the five local snapshots. The system never fetches the live website.
- **Paid API disclosure:** Anthropic Claude API.
  - **Claude Haiku 5.5** (`claude-haiku-5-5`): intent parsing when the rules find no product, the
    section picker, and prose-fact extraction at ingest.
  - **Claude Sonnet 5.5** (`claude-sonnet-5-5`): composition of free-text answers on the retrieval
    path.
  - Every call is cached in `submission/.cache/llm.sqlite` (not committed). A repeated identical
    request does not call the API again.
  - Separately, the harness's optional `--judge` flag uses Claude Opus 5.5. That is the scorer's own
    judge, not part of this system.
- **`KB_NO_LLM=1`** (or no `ANTHROPIC_API_KEY`) turns the LLM off, and the system still runs end to
  end:
  - rules-only intent parsing;
  - no prose facts beyond those in the committed facts cache;
  - the picker keeps the top hit only when the retrievers agree it ranks first;
  - an extractive answer from the top section instead of Sonnet.
  Expect lower accuracy on free-text questions.
- Any API failure (timeout after 8 s, 30 s for ingest extraction; rate limit; malformed output)
  returns `None` to the caller, which degrades the same way. A degraded answer is not memoised.
- Other environment variables: `KB_DB_PATH` (SQLite store; default `submission/.cache/kb.sqlite`,
  deleted at every `build()`) and `KB_LLM_CACHE` (default `submission/.cache/llm.sqlite`).

## Latest public-eval comparison

Local reruns on 2026-11-02 evaluated both implementations against the same 40 questions in
`eval/public_eval.jsonl`, without the optional `--judge` or `--conflicts-gold` flags.
The submission used the configured environment and existing local caches; these are not cold-start
measurements.

| Metric | `starter/` | `submission/` |
|---|---|---|
| Accuracy | 0.093 | 0.9271 |
| Tier disambiguation | 0.00 | 1.00 |
| Stale traps | 0.14 | 0.9357 |
| Final score | 0.093 | 0.9271 |
| p95 answer latency | 0.33 s | 0.25 s |
| Ingest time | ~0 s | 482.2 s |
| Errors | 0 | 0 |

Both runs reported 0 forbidden-value trap hits and paraphrase and repeat consistency of 1.0.
The submission is substantially more accurate, at the cost of ingest time. Its final score equals its
accuracy because p95 latency (0.25 s, from the memoised, warm-cache run) is under the harness's 2 s
threshold for full speed credit. The submission column was refreshed after generic questions about
products with a business version (credit cards, checking, share savings, money market, certificates)
stopped defaulting to `segment=personal`, which lifted dup-034 from 0.425 to 1.0; the starter column is
from the earlier session and was not re-run.

The submission report is `results/report.json` (latest run); the starter report is in the timestamped
`results/starter-rerun-*` directory from the earlier session.
These local artifacts are not committed. These results are not directly comparable to the older
judge-enabled run below, which also included the conflicts pillar scored against dev gold.

## Current scores (local, `dev/scores.csv`)

| Run | Public QA (40 items, `--judge`) | Held-out dev fixtures | Conflicts F1 (dev gold) | p95 latency |
|---|---|---|---|---|
| starter | 0.093 | – | – | 0.58 s |
| `conflicts@a0cc0d4` (latest scored) | 0.790 | 0.847 | 0.784 | 3.11 s |

In the latest public run, the harness reported final score 0.787 and ingest 387.6 s. These two
figures come from that local run's `results/score_run/conflicts/public/report.json`. `results/` is
not committed, and `dev/scores.csv` has no columns for them. The run found 0 errors, 0 stale-trap hits, and paraphrase and repeat consistency of 1.0. After a0cc0d4, only
`kb/conflicts.py` changed (how prose figures treat years). `dev/sample_conflicts.py --score` at 200bc53
still emits the same 29 conflicts with F1 0.784. p95 is above the
1.5 s target: the Haiku picker and Sonnet composer run synchronously on the retrieval path when their
cache is cold.

**Warm ingest is not fast.** Only LLM responses persist between runs (`submission/.cache/llm.sqlite`).
Every `build()` deletes the SQLite store, and the dense index deletes its Chroma folder at startup. A
second run therefore re-chunks, re-extracts and re-embeds every chunk with the ONNX model, and none of
that is cached. Local runs that reused the LLM cache still took 376–456 s to ingest, so the plan's
"under 2 min warm" target is not met. The hard limit of 30 min is met.

## Certificate scope and reporting-year policy

**Certificate scope.** A generic certificate question (no tier named) loads candidates for every scoped
product and reads back all terms rather than asking which term. Terms, specials (e.g. 17-Month
Certificate Special), jumbo tiers and their minimums, and segment restrictions are kept distinct, so a
request for one tier does not leak another tier's rate.

**Reporting year / reference date.** The reference date is, in order: an explicit `as_of`, a date in the
question, then the latest snapshot's taken-on date. The wall clock is never used. "Last year" means the
year before the reference year; with no period named, the latest year with a published fact is used. If
the requested year (or any annual VTO fact) does not exist, the system refuses instead of substituting
another year. Annual VTO counts are keyed `what=vto-employees;year=YYYY` (2023 = 294, 2024 = 256).

**Dev diagnostics.** `dev/completeness_temporal_eval.jsonl` holds certificate-inventory, tier and
annual-VTO cases; unavailable-year cases use `should_refuse` so the scorer checks refusal rather than
giving every empty-token answer zero.

**Benchmark caveats (gold not changed; seek organizer clarification).**
- dup-005 has no `must_include` tokens, so under the unjudged scorer it cannot reward a complete
  certificate listing; only the not-asking-which-term trap applies.
- dup-088 is ambiguous: its gold expects 294 (2023), but with a 2026 reference "last year" is 2025,
  which is not published, so the original item may remain at zero. We do not special-case it.

Live, judged and public-evaluation results were not measured in this change; only the deterministic
suite was run.
