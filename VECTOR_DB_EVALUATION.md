# Vector DB / Retrieval Store Evaluation

Date: 2026-10-31. Scope: choose the storage and search layer for the Dupaco knowledge system described in `PROJECT_PLAN.md`.

Inputs read: `PROJECT_PLAN.md`, `Knowledge-Hackathon-Assignment.pdf` (all 5 pages), and in the kit `harness/contract.py`, `harness/scoring.py`, `harness/run_eval.py`, `eval/public_eval.jsonl`, `harness/selftest/*`, the snapshot manifests and the pages.

Decisions from the user, relayed by the coordinator on 2026-10-31, are included throughout:
- The deadline is 2 days and the build is solo, so setup simplicity carries heavy weight.
- The grader has `ANTHROPIC_API_KEY` and network access.
- Precomputed extracted facts may be committed. The raw snapshot data must stay out of git, and the repo stays private.
- Grading uses the same 5 snapshots.
- "Conflict" means any value that changed over time.
- `as_of` is clamped to the first and last snapshots.

**Evidence labels used below.**
- **[measured]**: I ran it on this machine against the real data or packages (macOS x86_64, Python 3.11.13, SQLite 3.53.4).
- **[resolved]**: dependency trees resolved with `uv pip compile` for Linux x86_64 / py3.11, or wheel sizes from `pip download` or the PyPI JSON API.
- **[docs/memory]**: from documentation or my own knowledge, not checked here.

---

## 0. Bottom line

**A vector database should not be the core of this system.** The core should be **one stdlib `sqlite3` database**, held in memory and optionally persisted to a local cache file. It holds:
1. a temporal **fact table** (fact key → value per snapshot), which does the answering, the `as_of` handling and the conflict detection;
2. a **SQLite FTS5** keyword index over cleaned page sections, as the retrieval fallback and for the context the composer needs.

Dense vectors are an **optional third layer**: a `model2vec` static embedding (`potion-base-8M`, 256-dim) kept as a numpy matrix and fused into the FTS5 ranking. Add it only if a paraphrase test shows it helps.

The fallback, if FTS5 is somehow unavailable in the grader's Python, is `bm25s` with the same numpy matrix, all in memory.

The reasons in brief:
- The scored behaviours are exact-qualifier lookups, history over time, and refusal when a fact is missing. Similarity search does none of these.
- On the practice set, BM25 retrieved better than dense vectors **[measured]**.
- The stack is either no dependencies at all, or about 30 MB of wheels.

---

## 1. Retrieval patterns the system actually needs

| # | Pattern | Evidence | Who serves it |
|---|---|---|---|
| P1 | **Lookup by canonical fact key** `(product, attribute, qualifiers{term, tier/balance band, model-year, metric rate/APR/APY, segment})` | PDF §6.4: "48-month vs 60-month, rate vs APY, standard vs jumbo, personal vs business… different facts, not conflicts". The scorer zeroes an item on any `must_not_include` hit (`scoring.py`), and other-tier values are listed as traps: dup-045 (0.15, not 0.30/0.40), dup-046 (0.20 @ $25,000, not 0.35), dup-011 (7.13, not 6.7), dup-001, dup-016/017, dup-028, dup-035. | Fact table (SQL `WHERE` on key columns) |
| P2 | **Time-travel / `as_of` filter**: restrict to facts or chunks live in one resolved snapshot | PDF §1 and §3: "answer as the website stood on that day". dup-006 (`as_of=2026-05-31` → 3.90%, not 4.00%). PDF §5: the hidden set has more `as_of` questions. The selftest has `t2` with `as_of`. **New rule:** a date before 01 uses 01, a date after 05 uses 05, and a date in between uses the latest snapshot on or before it. | `snapshot_id` column on facts; `snaps` set on chunks |
| P3 | **History of one fact key across snapshots and pages**, i.e. "what values has X had, where, when" | PDF §6.2 "Keep history… *when* you saw each fact and *where*". `conflicts()` is scored by F1 and by the winner (`score_conflicts`), and a match needs ≥2 gold values or 1 value plus a topic keyword. **New definition: any value that changed over time is a conflict**, so every fact key with more than one distinct value across snapshots is a candidate. | Fact table: `GROUP BY fact_key HAVING COUNT(DISTINCT value) > 1` |
| P4 | **Authority / page-type ranking** inside a snapshot: rate page > product page > calculator > blog/press | dup-016: rate page 5.39% against the calculator's 5.34%. dup-087: Forbes "fifth" against older press "fourth" (Forbes appears on **88 pages** per snapshot **[measured]**). dup-073: 22 against "24 branch". dup-008/080/082: current URLs against stale `info.dupaco.com` / `/locations/branch-hours`. A dense query for "60-month auto loan APR" returns `/auto-loans/`, then **`/calculators/vehicle-affordability`**, then `/switch-and-earn` as its top 3 **[measured]**, so the stale calculator ranks second. | `page_type` + `authority` columns. A sort key, not a similarity score. |
| P5 | **Exact keyword, number and phrase match**: product names, "12-month", "Jumbo", "Bright Savings", "Home$tart", routing numbers, ZIPs, URLs | dup-054/055 (273974581), dup-068 ("11375 Oldenburg", "61036"), dup-031 ("15,000"), dup-050 (Bright Savings against Jump Start), dup-094 (Apple/Google/Samsung Pay), dup-008/080/082 (URLs as answers). The scorer checks numbers by value and URLs by substring. | FTS5 (phrase and prefix queries) plus regex extractors |
| P6 | **Lexical retrieval of passages for prose answers** (long tail: eligibility, VTO count, mobile wallets, Hy-Vee closure) | dup-081, dup-088 (294), dup-075/074/076 (Hy-Vee, "March 31, 2026", Key West/Sycamore), dup-104 (12:30, noon), dup-101. These feed the Sonnet composer (plan §2.2 step 4). | FTS5 BM25 limited to the resolved snapshot |
| P7 | **Paraphrase robustness**: reworded questions must give the same numbers | PDF §5: the hidden set "includes reworded versions". `score_consistency` compares the numeric gist across a `group`. | Mainly the **intent canonicalizer** (rules plus a cached Haiku classifier), *not* retrieval. Dense vectors are optional help here. |
| P8 | **Absence detection / honest refusal** | dup-145 (7/1 ARM), dup-147 (construction loan), dup-102 (Mullan Ave lobby hours). "construction loan" appears on **27–40 pages** per snapshot and "7/1" on **6–11** **[measured]**, so retrieval always finds *something*. With model2vec, the best cosine score for the refusal questions (0.794, 0.771) is **higher** than for answerable ones (dup-016 0.768, dup-028 0.694) **[measured]**. | Fact table: "no fact for key (product=construction_loan, attr=rate)". A similarity threshold cannot do this. |
| P9 | **Near-duplicate collapse across snapshots** (dedupe text so it is indexed once) | 613/633, 608/626, 618/633 and 256/680 pages change between consecutive snapshots even after cleaning **[measured]**. The cause is rotating "related posts" widgets, reCAPTCHA payloads and vehicle-inventory tables, not real content. 27,473 chunk postings collapse to **11,777 distinct chunks** **[measured]**. | Content hash per chunk/section, stored as a `chunk` table plus `chunk_snapshot` membership |
| P10 | **Source citation at path level** | `score_item`: +0.15 when a cited URL matches `expected_sources` at path level, e.g. `/personal/loans/home-loans#rates`, `/business/cash-management`. | `url` stored on every fact and chunk |
| P11 | **Determinism** (repeat pass gives the same numbers) | `score_consistency` part (b). The harness re-asks every k-th question. | Stable sort `(score DESC, url, id)`. No approximate-NN randomness. |

**Conclusion from the patterns:** P1–P4 and P8 hold most of the accuracy weight (55%), all of the conflict weight (15%) and the fact-level determinism. They are **relational queries over a few hundred facts**. Vector similarity can help only with P6 and P7, and even there BM25 does at least as well on this corpus (§4.1).

---

## 2. Data volume (measured unless noted)

| Quantity | Value |
|---|---|
| Pages per snapshot | 637 / 634 / 635 / 683 / 705 = **3,294 page versions**, **722 distinct URLs**, all `www.dupaco.com` (including 24 PDF URLs; `info.dupaco.com` appears only as links inside text) |
| Raw markdown | 32 MB on disk. 25.2 M chars (~6.3 M tokens at 4 chars/token). |
| Distinct page contents (exact hash) | 2,826 of 3,294, so page-level hashing barely dedupes |
| After cleaning (drop image/link targets, reCAPTCHA, VIN inventory rows, lines on ≥20% of pages) | 17.4 M chars total. 2,354 distinct pages. |
| Paragraphs | 149,455 total, **23,611 distinct** |
| Chunks (≤1,500 chars, heading-aware, title-prefixed) | **27,473 postings → 11,777 distinct**. About 5.7–7 M chars, ~1.4–1.8 M tokens. |
| FTS5 rows (paragraph level, url + para dedupe) | 41,389 rows. Built in **0.26 s**. |
| Embeddings, model2vec potion-base-8M (256-d float32) | 11,777 × 256 × 4 B = **11.5 MB**. Encoded in **1.2 s** on CPU. |
| Embeddings, 384-d (bge-small / MiniLM) | ~18 MB (computed) |
| Embeddings, 1024-d API (e.g. Voyage) | ~48 MB (computed). About 1.5–1.8 M input tokens. |
| Structured rate facts | Pages with %-bearing tables: 24–31 per snapshot. %-bearing table rows: 442–511 per snapshot, **433 distinct across all**. Distinct prose sentences with "x.xx% APR/APY": **91**. |

So the fact layer is in the **hundreds to low thousands of rows**, and the text layer is **about 12k chunks**. Both fit in memory with a large margin. At this size no ANN index is needed: brute-force numpy over 11.8k × 256 takes **1.2 ms** p95 **[measured]**.

---

## 3. Candidate comparison

Footprints are Linux x86_64 / py3.11 wheel downloads, including transitive dependencies **[resolved]**. Latency and ingest figures are **[measured]** where marked M, otherwise **[docs/memory]**.

| Candidate | Hybrid (vector + BM25) | Metadata / time filter | Install (Linux py3.11, no server) | Ingest for ~12k chunks | Query latency | Determinism | Persistence | License | Maturity | Fit |
|---|---|---|---|---|---|---|---|---|---|---|
| **SQLite (stdlib) + FTS5** | BM25 native. Vectors via sqlite-vec or numpy, fused by hand (RRF). | Full SQL. Joins to the fact table. | **0 packages** (FTS5 is compiled into CPython's bundled SQLite; checked in Python 3.11.13 here, M) | **0.26 s** (M) | p95 **41 ms** with post-filter by snapshot set (M). A naive JOIN + OR query took 3.5 s p95 (M), so the query shape matters. | Exact. Deterministic with a tiebreak on rowid. | Single file or `:memory:` | Public domain | Very high | **Best** |
| sqlite-vec (extension) | Vector KNN in SQLite, combined with FTS5 by hand | Metadata columns in `vec0`. Filtered KNN works (M). | 1 wheel, **160 KB**. Installed fine (M). Needs `enable_load_extension`, which some Python builds disable [docs/memory]. | Insert 11.8k: **0.17 s** (M) | Filtered KNN **1.7 ms** (M) | Exact brute force | File | MIT/Apache-2.0 | Pre-1.0 (0.1.9) | Good add-on, not needed |
| bm25s + numpy | BM25 plus a numpy dense matrix, RRF fused by hand | Boolean mask arrays (M) | 2 wheels, **16 MB** (mostly numpy) | Index 11.8k: **0.94 s** (M) | p95 **0.5 ms** (M) | Exact | `save()`/`load()` or rebuild | MIT | Medium, active | **Fallback** |
| rank_bm25 | BM25 only. Pure Python, slow on big corpora. | Manual | numpy only, 16 MB | Seconds | Tens of ms+ [docs/memory] | Exact | Pickle | Apache-2.0 | Unmaintained since 2022 [docs/memory] | Inferior to bm25s |
| FAISS (`faiss-cpu`) + bm25s | Dense only, BM25 separate | Not native (ID selectors only) | 3 wheels, **34 MB** | Fast | Sub-ms | Flat index is exact | `write_index` | MIT | High | Overkill at 12k vectors |
| DuckDB (+ VSS, FTS ext.) | FTS and VSS are separate **extensions downloaded at runtime** [docs/memory] | Full SQL | 1 wheel, **23 MB**. Installed (M). | Fast | Fast | Exact, except HNSW | File (VSS persistence is experimental) [docs/memory] | MIT | High core, extensions less so | Runtime extension download is a fresh-clone risk. SQLite already covers it. |
| LanceDB | Native hybrid (vector + Tantivy-style FTS) with rerankers | SQL-like `where` | 18 pkgs, **145 MB** (lancedb 77 MB + pyarrow 54 MB). **Failed to install on Intel macOS py3.11: no wheel, and the Rust source build failed** (M). Linux wheel exists. | Fast [docs/memory] | ms [docs/memory] | ANN non-exact unless brute force | Directory of Lance files | Apache-2.0 | Fast-moving, API churn | Works on Linux, but heavy and fragile on dev boxes. Its features aren't needed. |
| Chroma (embedded) | Dense plus document `where_document` contains-filter. BM25/sparse was newer or limited in my knowledge [docs/memory]. | Metadata `where` | **85 pkgs, 102 MB** (onnxruntime, tokenizers, grpc, otel…). Installed (M). | Default ONNX embedder is slow on CPU | ms | HNSW approximate | SQLite + HNSW dir | Apache-2.0 | Medium–high | Heavy dependency tree for little gain |
| Qdrant local mode (`qdrant-client`, `:memory:`/path) | Dense plus sparse vectors (BM25 via fastembed) | Rich payload filters | 20 pkgs, **28 MB**; 38 pkgs / ~85 MB with `[fastembed]` | Local mode is pure Python, OK at 12k [docs/memory] | Local mode is brute force, ms at 12k [docs/memory] | Exact in local mode | Path persistence | Apache-2.0 | High (server), local mode is a dev convenience | Reasonable, but adds a second store beside the fact table |
| Tantivy (python) | BM25 only | Fast fields / queries | 1 wheel, 5 MB | Fast | Sub-ms | Exact | Directory | MIT | Medium | Fine, but FTS5 is free |
| Milvus Lite | Dense plus sparse | Yes | 7 pkgs, **93 MB**. Linux/macOS only [docs/memory]. | — | — | — | File | Apache-2.0 | Medium | No benefit |
| **Postgres + pgvector** | Strong (tsvector + pgvector) | Full SQL | Needs a **running Postgres server**. `pip install` cannot provide it. | — | — | — | Server | PostgreSQL / MIT-like | Very high | **Does not fit** a "fresh clone, pip install, run harness" grader |
| **Weaviate** | Native hybrid | Yes | Server (Docker). The embedded mode downloads a binary at runtime and is Linux-only [docs/memory]. | — | — | — | Server | BSD-3 | High | **Does not fit** |
| **Milvus (standalone)** | Yes | Yes | Docker / k8s server | — | — | — | Server | Apache-2.0 | High | **Does not fit** |
| **Pinecone (hosted)** | Yes (sparse-dense) | Yes | Client only, but needs an account and a key the grader does not have, and **uploads the snapshot text/vectors to a third party**. That conflicts with "Don't redistribute it" (PDF §8). | Network-bound | Network RTT (tens to hundreds of ms) | Service-dependent | Remote | Proprietary | High | **Does not fit** |

The server options all fail the same test. The harness does `load_submission()` and then calls `ingest()` in-process (`run_eval.py`), and graders install only `requirements.txt` into a clean 3.11 env (PDF §7). Nothing starts a database server for us. A hosted service also adds a second vendor and key, plus a redistribution concern.

---

## 4. Embedding model choice

### 4.1 Does dense retrieval help on this corpus? (measured on the 40 public questions, snapshot 05 only, top-5 chunks)

| Retriever | Expected-source URL in top 5 (n=8 items with sources) | All `must_include` tokens present in top-5 text (n=36 answerable) | p95 query |
|---|---|---|---|
| bm25s | 2/8 | **20/36** | 0.5 ms |
| SQLite FTS5 (porter) | **3/8** | **22/36** | 41 ms (fixed shape) |
| model2vec potion-base-8M dense | 2/8 | 14/36 | 1.2 ms |
| Hybrid RRF (bm25s + model2vec) | 2/8 | 18/36 | 1.9 ms |

Takeaways:
- (a) **Lexical beats static-dense on this corpus.** Questions are full of exact tokens: product names, terms, numbers.
- (b) Naive fusion *lowered* the hit rate against BM25 alone.
- (c) **No retriever gets close to the needed accuracy alone.** Expected-source@5 is at most 3/8, which supports the plan's structured fact store as the core.
- Caveat: this is a 40-question, untuned check (no query rewriting, no authority prior, no stemming tweaks). It is evidence, not a benchmark.

### 4.2 Options

| Option | Install | Ingest time (11.8k chunks) | Determinism | Cost | Fresh-clone risk | Verdict |
|---|---|---|---|---|---|---|
| **model2vec `minishlab/potion-base-8M`** (static, 256-d) | `model2vec` + numpy + tokenizers, ~27 MB of wheels, **no torch**. Weights are a 29 MB safetensors file + 0.7 MB tokenizer, MIT license (M). | **1.2 s** (M) | Bit-identical re-encode (M) | Free | HF Hub download at first run, or vendor the 30 MB weights (MIT allows it) | **Pick, if any dense layer is used** |
| fastembed `BAAI/bge-small-en-v1.5` (ONNX, 384-d) | ~57 MB of wheels (onnxruntime). On Intel macOS, onnxruntime resolved to an older 1.23.2 (M). | **150 s per 1,000 chunks → ~29 min extrapolated** on this Intel Mac CPU (M). Probably 3–10× faster on a modern Linux grader [docs/memory], but this **endangers the 30-min ingest** limit without a prebuilt cache. | Bit-identical on the same machine (M). Can differ across CPUs/ORT versions [docs/memory]. | Free | Model download from HF/GCS | Better semantics, too slow and risky for the budget |
| sentence-transformers `all-MiniLM-L6-v2` | Linux py3.11 resolves to **torch 2.15.0 (514 MB) + ~18 CUDA 13 wheels** (cudnn 495 MB, cublas 386 MB, triton 272 MB, cusolver 217 MB, cufft 208 MB, nccl 205 MB…), **≈2.6 GB+** [resolved]. A CPU-only index URL fixes this but complicates `requirements.txt`. Torch also has no current Intel-macOS wheels [docs/memory]. | Minutes on CPU | Mostly deterministic on CPU | Free | Large install, slow clean-env install | **Drop from the plan** |
| API embeddings (e.g. Voyage AI; Anthropic has no first-party embeddings endpoint [docs/memory]) | Small client, plus a **second vendor key** (the grader only promises `ANTHROPIC_API_KEY`) | ~1.5–1.8 M tokens; minutes, rate-limit dependent [docs/memory] | Usually stable, not guaranteed bit-exact. Per-query network RTT adds 100–300 ms to every answer [docs/memory]. | Cents to low dollars | Needs network plus a key not on the grader. Sends snapshot text to another third party. | **Not worth it**, though the coordinator says it is allowed. Not tested (no snapshot data was sent anywhere). |

**Recommendation:** no dense layer in the minimum deliverable (Day 1). If the paraphrase fixture shows lexical misses that the intent canonicalizer can't fix, add **model2vec potion-base-8M**. It costs 1.2 s at ingest and ~1 ms per query, and has no torch. Pin the HF revision (`bf8b056651a2c21b8d2565580b8569da283cab23` observed) or vendor the 30 MB weights in the repo to remove the HF dependency.

---

## 5. Recommendation

### 5.1 Main pick: stdlib `sqlite3` (fact tables + FTS5), in-process
1. **It matches the patterns.** P1–P4 and P8 are SQL `WHERE` / `GROUP BY` on fact keys with `snapshot_id`. P3 under the **new "any change over time is a conflict" definition** is one query: `GROUP BY fact_key HAVING COUNT(DISTINCT value_norm) > 1`, ordered by `taken_on`. P5/P6 are FTS5 phrase and BM25 queries filtered to the resolved snapshot.
2. **Zero install risk.** FTS5 ships with CPython's SQLite (checked on 3.11.13). Ingest takes about 0.3 s for the text index. Queries are 1–41 ms.
3. **Deterministic and debuggable.** Exact scoring, stable `ORDER BY score, url, rowid`, and plain SQL you can inspect during a 2-day solo build. The fact table is also the conflict engine and the `as_of` engine, so there is only one store to keep correct.

### 5.2 Fallback: in-memory Python dicts + `bm25s` (+ numpy, + optional model2vec)
Use this if (a) the grader's Python lacks FTS5 (unlikely), or (b) the FTS5 query shape is too much trouble. Facts then live in plain dicts or dataclasses keyed by `fact_key`, with the same logic. This costs a 16 MB dependency and gives 0.5 ms queries.

### 5.3 Explicitly not chosen
- LanceDB, Chroma, Qdrant-local, Milvus-lite: 28–145 MB of dependencies, and a second store beside the fact table. LanceDB **failed to install** on the dev machine (M).
- DuckDB: extensions are downloaded at runtime.
- FAISS: pointless at 12k vectors.
- sentence-transformers: ≈2.6 GB on Linux.
- All server or hosted options (§3).

---

## 6. Proposed schema and index layout

All of this lives in one SQLite connection, `:memory:` during the run. Optionally write `.cache/ks.sqlite` locally (gitignored) to speed up development reruns. The tables:

```sql
-- snapshots and as_of resolution
CREATE TABLE snapshot (
  snapshot_id TEXT PRIMARY KEY, taken_on TEXT NOT NULL, ord INTEGER NOT NULL);
-- as_of rule (user decision): as_of < first -> first; as_of > last -> last;
-- otherwise latest snapshot with taken_on <= as_of.
--   SELECT snapshot_id FROM snapshot WHERE taken_on <= :d ORDER BY taken_on DESC LIMIT 1
--   (fallback when NULL: first snapshot by ord)

-- page versions (one row per url per snapshot)
CREATE TABLE page (
  page_id INTEGER PRIMARY KEY, snapshot_id TEXT, url TEXT, url_key TEXT,  -- url_key = host+path, lowercased, no trailing slash
  title TEXT, page_type TEXT,          -- rate_sheet|product|branch|calculator|blog|press|pdf|promo|legacy|other
  authority INTEGER,                   -- from page_type (plan §2.3)
  page_date TEXT,                      -- "Rates are effective as of 08/31/2026", press date, blog date
  content_hash TEXT,                   -- of CLEANED text (raw hash useless: ~95% of pages churn)
  UNIQUE(snapshot_id, url));

-- deduplicated text sections (the retrieval unit)
CREATE TABLE chunk (
  chunk_id INTEGER PRIMARY KEY, chunk_hash TEXT UNIQUE,
  url TEXT, heading_path TEXT, text TEXT);           -- title + heading path prefixed into text
CREATE TABLE chunk_snapshot (chunk_id INTEGER, snapshot_id TEXT, page_id INTEGER,
  PRIMARY KEY (chunk_id, snapshot_id, page_id));
CREATE VIRTUAL TABLE chunk_fts USING fts5(
  text, url UNINDEXED, snaps UNINDEXED,              -- snaps = "01,02,05": filter after ranking
  content='', tokenize="porter unicode61 tokenchars '$%.-/'");
  -- tokenchars keeps "12-month", "5.39%", "$15,000"-ish tokens together; verify against the
  -- scorer's numeric matching before committing (see risk R4).

-- the core: one observation per (fact key, page version)
CREATE TABLE fact_obs (
  obs_id INTEGER PRIMARY KEY,
  fact_key TEXT NOT NULL,              -- canonical, e.g. "auto_loan|apr|term=60|model_year=2024-27"
  product TEXT, attribute TEXT, qualifiers_json TEXT,   -- for partial-key queries (list intents)
  metric TEXT,                         -- rate|apy|apr|dividend|text|url|hours|address|count|date
  value_raw TEXT, value_norm TEXT,     -- "5.39% APR1" -> "5.39%"; hours normalised
  snapshot_id TEXT, taken_on TEXT, page_id INTEGER, url TEXT,
  page_type TEXT, authority INTEGER, page_date TEXT,
  quote TEXT,                          -- short excerpt -> Source.quote
  extractor TEXT,                      -- table|regex|llm_haiku@<prompt_ver>
  chunk_id INTEGER);
CREATE INDEX fo_key_snap ON fact_obs(fact_key, snapshot_id);
CREATE INDEX fo_prod_attr ON fact_obs(product, attribute, snapshot_id);

-- materialised at end of ingest
CREATE TABLE fact_resolved (           -- winner per key per snapshot (authority policy, plan §2.3)
  fact_key TEXT, snapshot_id TEXT, value_norm TEXT, obs_id INTEGER, losers_json TEXT,
  PRIMARY KEY (fact_key, snapshot_id));
CREATE TABLE answer_memo (             -- (canonical intent, snapshot) -> rendered Answer JSON
  intent_key TEXT, snapshot_id TEXT, answer_json TEXT, PRIMARY KEY (intent_key, snapshot_id));
```

**Conflict generation under the new definition (any value changed over time)**:
- *Across time:* for each `fact_key`, take the sequence of `fact_resolved.value_norm` ordered by `taken_on`. Each change (e.g. 3.90% → 4.00% for the 10-month jumbo) is one Conflict:
  - `values = [old, new]`
  - `resolution = value in 05`
  - `reason = "changed between <snap A> and <snap B>; latest snapshot wins"`
- *Within a snapshot:* among the `fact_obs` of one key in one snapshot, distinct values (e.g. calculator 5.34% against rate page 5.39%) form one Conflict with `reason = authority rule`.
- Merge both kinds into **one entry per fact key** so precision (F1) stays high. Name the topic with the product and qualifier words (e.g. "60-month auto loan APR 2024-27 model year") so `topic_keywords` match.

**Chunking.**
- Clean first: drop image markup, link targets, reCAPTCHA blocks, VIN inventory rows, and lines on ≥20% of pages.
- Split on markdown headings, at ≤1,500 chars. **Never split a table**: keep the header row with every table chunk.
- Prefix the page title and heading path.
- Hash the cleaned chunk text and index each distinct chunk once (11.8k). Snapshot membership lives in `chunk_snapshot` and the `snaps` column.

**Optional dense layer.** Keep `emb.npy` (N × 256, float32, row = `chunk_id`), built at ingest in ~1 s. Query: mask by the `chunk_snapshot` membership of the resolved snapshot, take the brute-force dot product, then RRF with FTS5 (k=60), with a stable tiebreak on `chunk_id`.

### 6.1 What ships prebuilt and what is built at ingest

| Artifact | Contains raw snapshot text? | Ship in git? | Notes |
|---|---|---|---|
| LLM fact-extraction cache (`facts_cache.jsonl`, keyed by **cleaned-chunk hash** + prompt version) | Derived values only (numbers, short normalised strings). The `quote` field would be raw text. | **Yes**, without `quote` (re-derive quotes at ingest from the local snapshot) | User approved committing extracted facts. This removes Haiku calls from grading-time ingest. |
| Synonym tables, intent rules, authority rules, page-type URL patterns | No | Yes | Hand-written |
| Intent-classification cache for known paraphrases (optional) | No (question text only) | Yes | Helps consistency and speed |
| model2vec weights (30 MB, MIT) | No | Optional (vendor) or pinned HF download | Vendoring removes the HF availability risk |
| SQLite DB with `chunk`/`chunk_fts`, page tables, `emb.npy` | **Yes** (FTS5 and chunk text are page text; embeddings are a reversible-ish derivative) | **No**, build at ingest | Takes < 5 s total, so shipping it gains nothing and risks the redistribution rule |
| `fact_obs` / `fact_resolved` | Values plus URLs | Not needed; rebuilt at ingest from the extractors and the cache in seconds | Keeps ingest the single source of truth |

---

## 7. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | Grader Python lacks FTS5 or `enable_load_extension` (some distro or pyenv builds) | FTS5 is in the official CPython builds and the `python:3.11` images [docs/memory]. Probe at `build()`: `CREATE VIRTUAL TABLE … USING fts5`, and on failure switch to the bm25s fallback (§5.2). Avoid sqlite-vec, so the load-extension issue does not arise. |
| R2 | FTS5 query shape is slow (JOIN + broad OR gave 3.5 s p95, M) | Use the measured shape: `MATCH … ORDER BY rank LIMIT 200`, then a Python filter on `snaps` (41 ms p95). Strip stop words and limit OR terms. |
| R3 | Over-trusting retrieval for refusals | Refusal is decided by fact-key absence plus an intent "known product?" check, never by a similarity threshold (P8 evidence: refusal cosines are higher than answerable ones). |
| R4 | Tokenizer splits "5.39%", "12-month", "$15,000", "7/1" differently from what is wanted | Test `tokenchars` choices. Normalise numbers in both the indexed text and the queries. Do the authoritative matching of numbers and qualifiers in the fact layer (regex), not FTS. |
| R5 | Page churn (95% of pages "change" each crawl) defeats per-page diffing | Hash per cleaned chunk. 11.8k distinct chunks; only the %-bearing ones (≈433 distinct table rows + ≈91 prose sentences) need rate extraction. |
| R6 | model2vec / HF download fails on the grader | The dense layer is optional and off by default. If enabled, vendor the weights or wrap the load in try/except → lexical only. |
| R7 | Conflicts F1 drops because the "any change" definition creates many candidates (blog dates, inventory) | Only emit conflicts for **fact keys** (canonical facts), never for raw chunk diffs. Merge per key. Run a precision audit against hand gold. |
| R8 | Determinism broken by tie order or dict ordering | Every ranking ends with a tiebreak on `(url, rowid)`. The answer memo is keyed by canonical intent. LLM composer outputs are cached. |
| R9 | Redistribution: a committed artifact contains page text | Commit no DB/FTS/embeddings or `quote` text. `.gitignore` `snapshots/`, `.cache/`, `*.sqlite`, `*.npy`. |

---

## 8. Changes to `PROJECT_PLAN.md`

1. **§2.2 step 2 and §3 "Performance budget": page-hash diffing does not work.** 613/633, 608/626, 618/633 and 256/680 pages change between consecutive snapshots even after boilerplate stripping (rotating "related posts", reCAPTCHA, inventory tables). Replace "only new/changed pages (by hash)" with **cleaned-chunk hashing** (27,473 → 11,777 distinct), and run LLM extraction only on new chunks that contain values. The plan's "≤600 calls" estimate is replaced by about 500 distinct rate-bearing rows/sentences plus selected prose chunks, already cached and committed (user approved).
2. **§2.2 step 7 and §2.4: retrieval index = stdlib SQLite FTS5** (not `rank_bm25`). Fallback is `bm25s`. `rank_bm25` is dropped (slower, unmaintained).
3. **§2.4 and §8: drop `sentence-transformers` / `all-MiniLM-L6-v2`.** It pulls ≈2.6 GB of torch and CUDA wheels on Linux py3.11. The optional dense layer becomes **`model2vec` potion-base-8M** (1.2 s ingest, ~1 ms query, no torch), off by default and kept only if the paraphrase fixture shows a gain. No API embeddings.
4. **§2.2 "Temporal store": use one SQLite schema (§6 above)** instead of ad-hoc dicts. `fact_obs` / `fact_resolved` / `answer_memo` replace `observations[...]` and `live_urls[...]`.
5. **§2.2 `conflicts()` and Phase 3: conflict = any change over time** (user decision), plus within-snapshot disagreements. Generate from the `fact_resolved` history, one entry per fact key. The plan §5 Phase 3 bullet "drop pure across-time updates where the old page is gone" is **reversed**.
6. **§2.2 answer step 1 and §9 Q6: `as_of` rule is settled.** Before the first snapshot → 01. After the last → 05. Otherwise the latest snapshot with `taken_on <= as_of`.
7. **§2.2 step 5 / Phase 1 "refusal gate": base it on fact-key absence and known-product checks, not retrieval confidence.** Measured refusal cosines exceed answerable ones.
8. **§2.4 and §9 Q2/Q3/Q4: API key available, cache committable, private repo.** Commit the fact-extraction cache (without raw `quote` text). Do **not** commit the FTS DB or embeddings; they rebuild in seconds. The no-key mode stays as a safety net but is no longer the primary graded path.
9. **§4 timeline (2-day solo):** Day 1 = SQLite schema, cleaning/chunking, FTS5, `as_of` resolver, table/regex extractors and resolver for rates, tiers and branches, refusal gate. Day 2 = conflicts from fact history, Haiku extraction cache for prose facts, Sonnet composer with memo, paraphrase fixture, clean-env test. The model2vec layer is a stretch goal only.
