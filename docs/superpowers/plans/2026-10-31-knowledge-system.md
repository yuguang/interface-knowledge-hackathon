# Temporal Knowledge System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `submission/solution.py` knowledge system that ingests the five Dupaco snapshots and answers member questions with the current (or as-of) value, the right tier, a source, honest refusals, and a precise `conflicts()` list.

**Architecture:** One foundation stage (Stage 1) fixes the repo, the shared types, the fact-key vocabulary, the SQLite store, the LLM wrapper and an orchestrator that already runs end to end on **stub** stage modules. Stage 2 is ten parallel tracks. Each track replaces exactly one stub module (or writes dev fixtures) and touches no other track's files. Stage 3 integrates the tracks, tunes on the public eval and ships.

**Tech Stack:** Python 3.11, stdlib `sqlite3` (FTS5) + `rank_bm25` fallback, ChromaDB (default ONNX embedder), Haystack (`haystack-ai`, `chroma-haystack`), `anthropic` SDK (Claude Haiku 5.5 `claude-haiku-5-5`, Claude Sonnet 5.5 `claude-sonnet-5-5`), pytest.

**Spec:** `PROJECT_PLAN.md` (repo root). Supporting evidence: `VECTOR_DB_EVALUATION.md`. Contract: `harness/contract.py`. Scorer: `harness/scoring.py`.

## Global Constraints

- Contract names in `harness/contract.py` are not renamed; `build()` returns an object with exactly `ingest`, `answer`, `conflicts`.
- Grading runs "in a clean Python 3.11 environment"; local dev uses a 3.11 venv (system Python is 3.9 — don't use it).
- Answer only from the snapshots. **No live website calls.** The only network client is `anthropic`.
- Each `answer` < 60 s hard; target p95 ≤ 1.5 s. Total ingest < 30 min hard; target < 10 min cold, < 2 min warm.
- Snapshot data never enters git (`snapshots_zip/`, `snapshots/0*/`, `submission/.cache/`). Repo is **private**.
- Committed extraction cache holds derived values only; `quote` is re-derived at ingest from local snapshots.
- Answer text never contains: stale values for the asked fact, chit-chat echoes, the phrases "in these facts", "context", or a clarifying question ("Which … would you like").
- Refusal text: contains "isn't listed on the website" and "please contact Dupaco", `confident=False`, and **no digits** beyond those in the question.
- Determinism: deterministic via the LLM cache + answer memo (the pinned SDK/API rejects `temperature`), stable sorts (score desc, then url asc, then chunk_id asc), memoized answers.
- One `Conflict` per fact key (PROJECT_PLAN §9.5): `values` = every distinct value across snapshots, `resolution` = snapshot-05 winner.
- `as_of`: before 2026-05-12 → `01_…`; after 2026-09-28 → `05_…`; otherwise latest snapshot with `taken_on <= as_of`. Explicit `as_of` argument beats a date phrase in the question.
- `requirements.txt` pins every runtime package (`anthropic`, `haystack-ai`, `chroma-haystack`, `chromadb`, `rank_bm25`) to the version `pip freeze` shows after Task 1.1.

## Review Focus

1. **Rate with a footnote suffix or odd spacing** ("5.39% APR1", "5.390 %"): must normalise to `5.39%` and never leak "APR1" or the footnote digit as a number. → Track C1 test `test_footnote_suffix_stripped`.
2. **Question date phrase with no `as_of` arg** ("as of May 2026", "back in June"): month/year must resolve to month end; unparseable phrases fall back to snapshot 05, never crash. → Task 1.2 test `test_question_date_phrases`.
3. **Tier boundary values** ("exactly $25,000", "$24,999.99", "$100,000 jumbo"): must pick the band containing the amount, inclusive lower bound. → Track E test `test_tier_boundaries`.
4. **Product that exists on the site but the attribute doesn't** ("construction loan rate", "7/1 ARM rate"): no fact + picker "none" → refusal with no digits, even though retrieval finds many matching pages. → Track I test `test_refusal_has_no_digits` and Stage 3 Task 3.2 refusal fixture.
5. **Anthropic call fails or times out at grading** (no key, 429, 8 s stall): every LLM caller gets `None` and degrades (extractive answer, rules intent, no prose facts) without raising. → Task 1.3 test `test_llm_failure_returns_none`.

---

## File Structure

```
/Users/yuguang/Projects/interface/          # repo root (git)
  harness/ eval/ starter/ snapshots/README.md   # copied from kit, unchanged
  snapshots_zip/  snapshots/0*/                 # gitignored data
  submission/
    solution.py                 # build() -> KnowledgeSystem          [1.3]
    requirements.txt            # pinned runtime deps                  [1.1]
    NOTES.md  README.md (root)  #                                      [3.4]
    data/facts_cache.jsonl      # committed LLM extraction cache       [D]
    kb/
      model.py                  # shared dataclasses + constants       [1.2]
      keys.py                   # fact-key vocabulary + value normalisers [1.2]
      snapshots.py              # registry + as_of resolution          [1.2]
      store.py                  # SQLite schema + DAO                  [1.2]
      llm.py                    # cached, fail-soft Anthropic wrapper  [1.3]
      system.py                 # orchestrator (owns all glue)         [1.3]
      normalize.py              # boilerplate + chunker                [A]
      classify.py               # page type, authority, page date      [B]
      extract/tables.py         # markdown rate tables -> FactObs      [C1]
      extract/regex.py          # hours/address/phone/routing/...      [C2]
      extract/llm_facts.py      # Haiku prose extraction               [D]
      resolve.py                # authority policy + intent selection  [E]
      conflicts.py              # Conflict builder                     [F]
      retrieval/fts.py dense.py pipeline.py picker.py   #              [G]
      intent.py                 # question -> Intent                   [H]
      compose.py                # templates, prose, sanitizer, refusal [I]
  tests/
    conftest.py fixtures/mini_site/   # synthetic hand-written pages   [1.2]
    test_<module>.py                   # one per module, owned by its track
  dev/                                 # eval fixtures + scripts       [J]
```

Rule for Stage 2: a track edits only its own files plus its own `tests/test_*.py`. `kb/keys.py` vocabulary lists are **append-only** (add a product/attribute in a one-line change; never rename).

## Dependency Graph

```
Stage 1 (sequential):  1.1 Repo/env ─► 1.2 Core types+store ─► 1.3 LLM+orchestrator+stubs  [tag: foundation]
Stage 2 (parallel, all start after `foundation`):
   A normalize   B classify   C1 tables   C2 regex   D llm_facts
   E resolve     F conflicts  G retrieval H intent   I compose    J dev fixtures
Stage 3 (sequential):
   3.1 Retrieval path  (needs A,B,G,I)        [tag: min-deliverable, public ≥ 0.35]
   3.2 Fact path       (needs C1,C2,D,E,H)    [public ≥ 0.65, 0 stale_trap_hits]
   3.3 Conflicts       (needs F, J)           [dev F1 ≥ 0.6]
   3.4 Hardening + submission                 [tag: submission]
```

Suggested ordering if parallelism is limited (solo, 2 days): Day 1 = Stage 1, then A, B, G, I (→ 3.1), then C1, C2, E, H. Day 2 = D, F, J, 3.2, 3.3, 3.4.

---

## Stage 1 — Foundation

### Task 1.1: Repo, environment, harness baseline

**Files:**
- Create: `.gitignore`, `submission/requirements.txt`, `requirements-dev.txt`, `pytest.ini`
- Copy (unchanged) from `hackathon-knowledge-kit-participant/`: `harness/`, `eval/`, `starter/`, `snapshots/README.md`, `snapshots_zip/` (ignored)

**Interfaces:**
- Produces: repo layout above; `.venv` with Python 3.11; `pytest.ini` with `pythonpath = submission harness` and marker `real: needs local snapshots`.

- [ ] **Step 1:** `git init` in `/Users/yuguang/Projects/interface`; write `.gitignore` with `snapshots_zip/`, `snapshots/0*/`, `submission/.cache/`, `results/`, `.venv/`, `__pycache__/`, `*.swp`, `.DS_Store`, `hackathon-knowledge-kit-participant/`.
- [ ] **Step 2:** Copy kit dirs to root. Create `.venv` with `python3.11 -m venv .venv` (install 3.11 via `brew install python@3.11` or `uv python install 3.11` if missing).
- [ ] **Step 3:** `pip install anthropic haystack-ai chroma-haystack chromadb rank_bm25` and `pip install pytest`; write `pip freeze` lines for the five runtime packages into `submission/requirements.txt`, pytest into `requirements-dev.txt`.
- [ ] **Step 4:** Run `python harness/run_eval.py --submission starter --eval eval/public_eval.jsonl --judge --out results/starter`.
  Expected: snapshots unpack into `snapshots/`, report prints, overall ≈ 0.09. Save the summary line to `dev/scores.csv` (header `commit,public,heldout,conflicts_f1,p95`).
- [ ] **Step 5:** Verify `git status --porcelain | grep -E 'snapshots/0|\.zip'` prints nothing. Create the private GitHub repo (`gh repo create --private`). Commit `chore: repo skeleton, kit harness, pinned deps`.

### Task 1.2: Shared types, fact-key vocabulary, as_of registry, SQLite store

**Files:**
- Create: `submission/kb/__init__.py`, `submission/kb/model.py`, `submission/kb/keys.py`, `submission/kb/snapshots.py`, `submission/kb/store.py`
- Create: `tests/conftest.py`, `tests/fixtures/mini_site/{01,02,05}/manifest.json + pages/*.md`
- Test: `tests/test_keys.py`, `tests/test_snapshots.py`, `tests/test_store.py`

**Interfaces (Produces — every Stage 2 track depends on these exact names):**

`kb/model.py`
```python
PAGE_TYPES = ("rate_sheet","product","branch","disclosure","other","calculator","promo","blog","news","legacy")
AUTHORITY = {"rate_sheet":100,"product":80,"branch":70,"disclosure":60,"other":40,
             "calculator":30,"promo":25,"blog":15,"news":10,"legacy":5}
NUMERIC_ATTRS = {"apr","apy","rate","min_balance","count"}

@dataclass(frozen=True) class PageInfo:  page_type: str; authority: int; page_date: Optional[str]  # ISO
@dataclass(frozen=True) class Chunk:     hash: str; url: str; title: str; heading_path: str; text: str
@dataclass(frozen=True) class StoredChunk: chunk_id: int; chunk: Chunk
@dataclass(frozen=True) class PageContext:
    snapshot_id: str; taken_on: str; url: str; title: str; info: PageInfo
    markdown: str            # cleaned
    chunks: list[StoredChunk]
@dataclass(frozen=True) class FactObs:
    fact_key: str; product: str; attribute: str; qualifiers: dict[str,str]
    value_raw: str; value_norm: str
    snapshot_id: str; taken_on: str; url: str; page_type: str; authority: int
    page_date: Optional[str]; quote: str        # ≤ 200 chars
    extractor: str                              # "table" | "regex" | "llm:<prompt_ver>"
    chunk_id: Optional[int] = None
@dataclass(frozen=True) class ResolvedFact:
    fact_key: str; value: str; winner: FactObs; losers: list[FactObs]; rule: str
@dataclass(frozen=True) class Intent:
    family: str                       # "fact" | "list" | "prose"
    product: Optional[str]; attribute: Optional[str]; qualifiers: dict[str,str]
    superlative: Optional[str]        # "highest" | "lowest" | None
    cleaned_question: str
    canonical: str                    # memo key; equal for paraphrases
@dataclass(frozen=True) class Hit:
    chunk_id: int; url: str; heading_path: str; text: str; score: float; snapshot_id: str
```

`kb/keys.py`
```python
PRODUCTS = ("auto_loan","mortgage_fixed","mortgage_arm","heloc","home_equity_loan","personal_loan",
            "student_loan","construction_loan","rv_loan","certificate","certificate_special",
            "share_savings","money_market","checking","bright_savings","jump_start","ira",
            "credit_card","branch","org","program")
ATTRIBUTES = ("apr","apy","rate","min_balance","hours_lobby","hours_driveup","address","phone",
              "status","closure_date","routing_number","count","url","award","eligibility","detail")
QUALIFIERS = ("term","tier","loan_amount","model_year","segment","kind","name","day","what")
def make_key(product: str, attribute: str, qualifiers: dict[str,str]) -> str
    # "auto_loan|apr|model_year=2024-2027;term=60" — qualifiers sorted by name; ValueError on unknown names
def parse_key(key: str) -> tuple[str, str, dict[str,str]]
def norm_percent(s: str) -> Optional[str]      # "5.390 %"->"5.39%", "5.39% APR1"->"5.39%", "0.15"->"0.15%"
def norm_money(s: str) -> Optional[str]        # "$15,000.00"->"$15,000", "$2.50"->"$2.50"
def norm_term_months(s: str) -> Optional[int]  # "60 Months"->60, "5 years"->60, "17-month"->17
def norm_band(s: str) -> Optional[str]         # "$10,000 - $24,999"->"10000-24999", "$25,000+"->"25000-inf", "up to $100,000"->"0-100000"
def norm_model_year(s: str) -> Optional[str]   # "2024-27 Model Year"->"2024-2027", "2016 Model Year (and older)"->"0-2016"
def band_contains(band: str, amount: float) -> bool   # inclusive both ends; "inf" upper
```

`kb/snapshots.py`
```python
class SnapshotRegistry:
    def add(self, snapshot_id: str, taken_on: str) -> None
    def ids(self) -> list[str]                  # ordered by taken_on
    def latest(self) -> str
    def taken_on(self, snapshot_id: str) -> str
    def resolve(self, as_of: Optional[str]) -> str
def parse_question_date(question: str) -> Optional[str]
    # "as of May 2026"->"2026-05-31"; "as of May 15, 2026"->"2026-05-15"; "2026-06-01"->same;
    # "in June 2026"->"2026-06-30"; anything else -> None
def effective_as_of(question: str, as_of: Optional[str]) -> Optional[str]   # arg wins over text
```

`kb/store.py` — schema exactly as `VECTOR_DB_EVALUATION.md` §6 (`snapshot`, `page`, `chunk`, `chunk_snapshot`, `fact_obs`, `fact_resolved`, `answer_memo`) except: `chunk_fts` is a plain FTS5 table `fts5(text, tokenize="porter unicode61")` with `rowid = chunk_id` (snapshot filtering is a join on `chunk_snapshot`, not a `snaps` column).
```python
class Store:
    def __init__(self, path: str = ":memory:")       # probes FTS5; sets self.fts_available
    def add_snapshot(self, snapshot_id: str, taken_on: str) -> None
    def add_page(self, snapshot_id: str, url: str, title: str, info: PageInfo, content_hash: str) -> int
    def add_chunks(self, snapshot_id: str, page_id: int, chunks: list[Chunk]) -> list[StoredChunk]  # dedup by hash
    def new_chunks(self, snapshot_id: str) -> list[StoredChunk]   # chunks first seen in this snapshot
    def chunk(self, chunk_id: int) -> StoredChunk
    def chunk_ids_in(self, snapshot_id: str) -> set[int]
    def fts_match(self, match_expr: str, snapshot_id: str, k: int) -> list[tuple[int, float]]  # (chunk_id, -bm25), best first
    def live_urls(self, snapshot_id: str) -> set[str]
    def add_facts(self, obs: list[FactObs]) -> None
    def facts(self, *, fact_key: str = None, product: str = None, attribute: str = None,
              snapshot_id: str = None) -> list[FactObs]          # sorted (fact_key, taken_on, url)
    def fact_keys(self, snapshot_id: Optional[str] = None) -> list[str]
    def set_resolved(self, snapshot_id: str, resolved: list[ResolvedFact]) -> None
    def resolved(self, snapshot_id: str, product: Optional[str] = None,
                 attribute: Optional[str] = None) -> list[ResolvedFact]
    def memo_get(self, key: str, snapshot_id: str) -> Optional[dict]
    def memo_put(self, key: str, snapshot_id: str, answer: dict) -> None
```

**Fixture:** `tests/fixtures/mini_site/` — three hand-written snapshots (`01_2026-05-12_baseline`, `02_2026-05-19_recrawl`, `05_2026-09-28_latest`) in the kit's manifest format, ~8 pages each, **written by hand, not copied from the site**: an auto-loans page with the model-year/term/APR table shape seen in the real site (`| 2024-27 Model Year | 60 Months | 5.39% APR1 |`), a calculator page saying 5.34%, a certificate-specials page (10-month jumbo 3.90% in 01/02, 4.00% in 05), a share-savings tier table, a branch page with hours, a press release ("fourth" in 01, "fifth" in 05), a page with routing number 273974581, and a page repeated nav/footer lines. `conftest.py` provides `mini_snapshots() -> list[Snapshot]` (via harness `load_snapshots`) and `real_snapshots()` that `pytest.skip`s when `snapshots/05_2026-09-28_latest` is absent.

- [ ] **Step 1: Write failing tests**
```python
# tests/test_keys.py
def test_make_key_sorted():  assert make_key("auto_loan","apr",{"term":"60","model_year":"2024-2027"}) == "auto_loan|apr|model_year=2024-2027;term=60"
def test_parse_roundtrip():  assert parse_key("auto_loan|apr|model_year=2024-2027;term=60") == ("auto_loan","apr",{"model_year":"2024-2027","term":"60"})
def test_unknown_qualifier(): with pytest.raises(ValueError): make_key("auto_loan","apr",{"color":"red"})
@pytest.mark.parametrize("s,v",[("5.390 %","5.39%"),("5.39% APR1","5.39%"),("4.00% APY","4.00%"),("0.15","0.15%")])
def test_norm_percent(s,v): assert norm_percent(s) == v
def test_norm_band():  assert norm_band("$10,000 - $24,999")=="10000-24999" and norm_band("$25,000+")=="25000-inf"
def test_norm_term():  assert norm_term_months("60 Months")==60 and norm_term_months("5 years")==60 and norm_term_months("17-month")==17
def test_model_year(): assert norm_model_year("2024-27 Model Year")=="2024-2027"
def test_band_contains(): assert band_contains("10000-24999",24999) and not band_contains("10000-24999",25000) and band_contains("25000-inf",10**7)
# tests/test_snapshots.py
@pytest.mark.parametrize("as_of,sid",[(None,"05"),("2026-01-01","01"),("2026-05-12","01"),("2026-05-31","02"),
                                      ("2026-08-26","03"),("2026-09-28","05"),("2027-03-01","05")])
def test_resolve(registry_all5, as_of, sid): assert registry_all5.resolve(as_of).startswith(sid)
@pytest.mark.parametrize("q,d",[("rate as of May 2026?","2026-05-31"),("as of May 15, 2026","2026-05-15"),
                               ("in June 2026","2026-06-30"),("back last year","None"),("what is it now","None")])
def test_question_date_phrases(q,d): assert str(parse_question_date(q)) == d
def test_arg_beats_text(): assert effective_as_of("as of May 2026", "2026-09-01") == "2026-09-01"
# tests/test_store.py
def test_chunk_dedup_across_snapshots(): # same Chunk added in 01 and 02 -> one chunk row, two chunk_snapshot rows; new_chunks("02") == []
def test_fts_filters_by_snapshot():      # chunk only in 01 not returned by fts_match(..., "02_...", 5)
def test_facts_roundtrip():              # add_facts then facts(product="auto_loan") returns equal FactObs list
def test_resolved_roundtrip():           # set_resolved / resolved(sid, "auto_loan","apr") preserves winner and losers
def test_memo_roundtrip()
```
- [ ] **Step 2:** Run `pytest tests/test_keys.py tests/test_snapshots.py tests/test_store.py -v` → FAIL (ImportError).
- [ ] **Step 3:** Implement the four modules with the signatures above. `Store` serialises `FactObs`/`ResolvedFact` via `dataclasses.asdict` + JSON in `qualifiers_json` / `losers_json`. If FTS5 is missing, `fts_available=False` and `fts_match` raises `RuntimeError` (Track G handles fallback).
- [ ] **Step 4:** Run the same command → PASS.
- [ ] **Step 5:** Commit `feat(kb): shared types, fact keys, as_of registry, sqlite store`.

### Task 1.3: LLM wrapper, stage stubs, orchestrator, `build()`

**Files:**
- Create: `submission/kb/llm.py`, `submission/kb/system.py`, `submission/solution.py`
- Create stubs (final signatures, trivial bodies): `kb/normalize.py`, `kb/classify.py`, `kb/extract/{__init__,tables,regex,llm_facts}.py`, `kb/resolve.py`, `kb/conflicts.py`, `kb/retrieval/{__init__,fts,dense,pipeline,picker}.py`, `kb/intent.py`, `kb/compose.py`
- Test: `tests/test_llm.py`, `tests/test_system.py`

**Interfaces (Produces — the stage signatures each Stage 2 track must keep):**

`kb/llm.py`
```python
HAIKU = "claude-haiku-5-5"; SONNET = "claude-sonnet-5-5"
class LLM:
    def __init__(self, cache_path: Path, enabled: Optional[bool] = None)  # enabled defaults to bool(ANTHROPIC_API_KEY) and KB_NO_LLM != "1"
    def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int = 512, timeout_s: float = 8.0) -> Optional[dict]
    def text(self, model: str, system: str, user: str, max_tokens: int = 400, timeout_s: float = 8.0) -> Optional[str]
    # temperature 0; cache key sha256(model, system, user, schema); cache = sqlite file at cache_path;
    # any exception / timeout / disabled -> None (never raises). Structured JSON via the Messages API's
    # JSON-schema output (check the claude-api skill for the current parameter name).
```

Stage stubs (signature → stub behaviour):
| Module | Signature | Stub returns |
|---|---|---|
| `normalize` | `fit_boilerplate(pages: list[Page]) -> frozenset[str]` | `frozenset()` |
| | `clean(markdown: str, boilerplate: frozenset[str]) -> str` | input unchanged |
| | `chunk_page(url: str, title: str, cleaned: str) -> list[Chunk]` | one chunk per blank-line paragraph |
| `classify` | `classify_page(url: str, title: str, markdown: str, taken_on: str) -> PageInfo` | `PageInfo("other", 40, None)` |
| `extract.tables` / `extract.regex` | `extract(ctx: PageContext) -> list[FactObs]` | `[]` |
| `extract.llm_facts` | `extract(ctx: PageContext, llm: LLM) -> list[FactObs]` | `[]` |
| `resolve` | `resolve_snapshot(store: Store, snapshot_id: str) -> list[ResolvedFact]` | `[]` |
| | `select_for_intent(intent: Intent, resolved: list[ResolvedFact]) -> list[ResolvedFact]` | `[]` |
| | `stale_values(store: Store, selected: list[ResolvedFact]) -> set[str]` | `set()` |
| `conflicts` | `build_conflicts(store: Store, registry: SnapshotRegistry) -> list[Conflict]` | `[]` |
| `retrieval.pipeline` | `class Retriever: __init__(store: Store, llm: LLM, index_dir: Path); index_snapshot(snapshot_id: str) -> None; search(question: str, snapshot_id: str, k: int = 10) -> list[Hit]; pick(question: str, hits: list[Hit]) -> list[Hit]` | `search` = `store.fts_match` with an OR of question words ≥ 3 chars; `pick` = `hits[:1]` |
| `intent` | `parse(question: str, llm: Optional[LLM] = None) -> Intent` (system passes its `llm`) | `Intent("prose", None, None, {}, None, q, q.lower().strip())` |
| `compose` | `render_facts(question: str, intent: Intent, selected: list[ResolvedFact], snapshot_id: str) -> Answer` | value list joined |
| | `render_prose(question: str, intent: Intent, hits: list[Hit], snapshot_id: str, llm: LLM) -> Answer` | `hits[0].text[:600]` with Source |
| | `refusal(question: str) -> Answer` | final refusal copy (see Global Constraints) |
| | `sanitize(answer: Answer, keep: set[str], drop: set[str]) -> Answer` | unchanged |

`kb/system.py` — `class KnowledgeSystem` owns all glue (no track edits this file in Stage 2):
```python
def ingest(self, snapshot):
    registry.add; store.add_snapshot; bp = normalize.fit_boilerplate(snapshot.pages)
    for page in snapshot.pages (sorted by url):
        info = classify.classify_page(...); md = normalize.clean(page.markdown, bp)
        page_id = store.add_page(..., sha256(md)); stored = store.add_chunks(sid, page_id, normalize.chunk_page(url, title, md))
        ctx = PageContext(...); store.add_facts(tables.extract(ctx) + regex.extract(ctx) + llm_facts.extract(ctx, llm))
    store.set_resolved(sid, resolve.resolve_snapshot(store, sid)); retriever.index_snapshot(sid); self._conflicts = None
def answer(self, question, as_of=None):
    sid = registry.resolve(effective_as_of(question, as_of)); it = intent.parse(question, llm)
    memo hit on (it.canonical, sid) -> return
    sel = resolve.select_for_intent(it, store.resolved(sid, it.product, it.attribute)) if it.product else []
    if sel: ans = compose.render_facts(question, it, sel, sid)
    else:
        picked = retriever.pick(it.cleaned_question, retriever.search(it.cleaned_question, sid))
        ans = compose.render_prose(question, it, picked, sid, llm) if picked else compose.refusal(question)
    ans = compose.sanitize(ans, keep={r.value for r in sel}, drop=resolve.stale_values(store, sel))
    memo_put; return ans
def conflicts(self): cached resolve of conflicts.build_conflicts(store, registry)
```
Every stage call in `answer` is wrapped so an exception falls through to `compose.refusal` (never crash). DB path: `submission/.cache/kb.sqlite` (deleted at `build()`), overridable via `KB_DB_PATH`. LLM cache: `submission/.cache/llm.sqlite`.

**Refusal decision (resolves PROJECT_PLAN §10):** refuse when there is **no selected fact** for the intent **and** the section picker returns nothing. Retrieval score alone never triggers or blocks a refusal.

**Chunk dedup decision (resolves PROJECT_PLAN §10):** the dedup unit is the cleaned chunk (hash of cleaned text); one `chunk` row per distinct text, membership in `chunk_snapshot`; FTS5 and Chroma index each distinct chunk once and filter by snapshot membership at query time.

`submission/solution.py`: adds its own directory to `sys.path`, imports the contract the same way the starter does, `def build(): return KnowledgeSystem()`.

- [ ] **Step 1: Write failing tests**
```python
# tests/test_llm.py
def test_disabled_returns_none(tmp_path): assert LLM(tmp_path/"c.sqlite", enabled=False).text(HAIKU,"s","u") is None
def test_llm_failure_returns_none(tmp_path, monkeypatch):  # patch client.messages.create to raise / sleep past timeout -> None
def test_cache_hit_skips_call(tmp_path, monkeypatch):     # second identical call does not invoke the client
# tests/test_system.py
def test_end_to_end_mini(mini_snapshots):
    ks = build(); [ks.ingest(s) for s in mini_snapshots]
    a = ks.answer("What is the APR for a 60-month auto loan?"); assert isinstance(a, Answer) and a.sources
    b = ks.answer("What is the APR for a 60-month auto loan?", as_of="2026-05-15")
    assert all(s.snapshot_id.startswith("01") for s in b.sources)
    assert isinstance(ks.conflicts(), list)
def test_deterministic(mini_snapshots): # same question twice -> identical text
def test_stage_exception_yields_refusal(mini_snapshots, monkeypatch): # make intent.parse raise -> answer.confident is False
```
- [ ] **Step 2:** `pytest tests/test_llm.py tests/test_system.py -v` → FAIL.
- [ ] **Step 3:** Implement `llm.py`, all stubs, `system.py`, `solution.py`.
- [ ] **Step 4:** `pytest -v` → all PASS. Then `KB_NO_LLM=1 python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl --out results/foundation` → runs with zero crashes/timeouts; score ≈ starter.
- [ ] **Step 5:** Commit `feat(kb): orchestrator, fail-soft LLM, stage stubs`; `git tag foundation`.

---

## Stage 2 — Parallel tracks (all start from tag `foundation`)

Each track: own branch `track/<letter>-<name>`, own module, own test file. Real-data tests are marked `@pytest.mark.real`.

### Track A: Normalizer and chunker

**Files:** Modify `submission/kb/normalize.py`; Test `tests/test_normalize.py`
**Interfaces:** Consumes `Chunk`, `Page`. Produces the three `normalize` signatures from Task 1.3.

Values from spec: boilerplate = a line appearing on ≥ 20% of the snapshot's pages; drop image markup, link targets (keep link text), reCAPTCHA blocks, VIN inventory rows; split on headings at ≤ 1,500 chars; **never split a table** and repeat the header row in every chunk of a long table; prefix `title > heading path` into `text`; `hash = model.chunk_hash(url, text)` (identity is per URL — ruling R6).

- [ ] **Step 1: Write failing tests**
```python
def test_boilerplate_threshold():  # line on 3 of 10 pages kept-out (≥20%), line on 1 of 10 kept
def test_clean_strips_images_and_links(): assert clean("![x](a.png) see [rates](https://x/y)", frozenset()) == "see rates"
def test_table_never_split():      # 200-row table -> every chunk containing table rows starts with the header row + separator
def test_heading_path_prefix():    # chunk.text.startswith("Auto Loans > Rates\n")
def test_chunk_max_len():          # all len(c.text) <= 1500 + len(prefix) for prose-only input
@pytest.mark.real
def test_real_dedup_ratio(real_snapshots):  # distinct chunk hashes across 5 snapshots: record the count in the report and assert <= 30,000 (11,777 was measured with text-only hashing; per-URL hashing yields more — re-baseline here and tell Track G)
```
- [ ] **Step 2:** `pytest tests/test_normalize.py -v` → FAIL.
- [ ] **Step 3:** Implement. Recaptcha/VIN detection: drop lines matching `recaptcha|g-recaptcha` and table rows containing a 17-char VIN `[A-HJ-NPR-Z0-9]{17}`.
- [ ] **Step 4:** `pytest tests/test_normalize.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(normalize): boilerplate stripping and table-safe chunker`.

### Track B: Page classifier

**Files:** Modify `submission/kb/classify.py`; Test `tests/test_classify.py`
**Interfaces:** Produces `classify_page(url, title, markdown, taken_on) -> PageInfo` with `authority = AUTHORITY[page_type]`.

URL rules (first match wins): host `info.dupaco.com` → `legacy`; path contains `/calculators/` or `-calculator` → `calculator`; `/news/`, `/press`, `/about/news` → `news`; `/learn/blog/`, `/YYYY/MM/DD/` → `blog`; `.pdf` or `/disclosures` → `disclosure`; path ends in `rates` or page has ≥ 1 table whose header contains APR/APY/Dividend and title contains "Rates" → `rate_sheet`; `/locations/` or `/branches/` → `branch`; `/promo`, `/special`, or title contains "Special" without a rate table → `promo`; `/personal/` or `/business/` → `product`; else `other`. `page_date`: first match of "effective (as of )?<date>", "Rates are effective as of MM/DD/YYYY", rate generation date, or a blog/press URL date → ISO.

- [ ] **Step 1: Write failing tests**
```python
@pytest.mark.parametrize("url,t",[("https://info.dupaco.com/auto","legacy"),("https://www.dupaco.com/calculators/auto-refinance","calculator"),
  ("https://www.dupaco.com/2018/09/25/understand-insurance-deductibles/","blog"),("https://www.dupaco.com/personal/loans/auto-loans","product"),
  ("https://www.dupaco.com/business/rates","rate_sheet")])
def test_url_types(url,t): assert classify_page(url,"","", "2026-09-28").page_type == t
def test_page_date_effective(): assert classify_page(u,"","Rates are effective as of 08/31/2026","2026-09-28").page_date == "2026-08-31"
def test_authority_order(): assert AUTHORITY["rate_sheet"] > AUTHORITY["product"] > AUTHORITY["calculator"] > AUTHORITY["news"] > AUTHORITY["legacy"]
@pytest.mark.real
def test_real_histogram(real_snapshots):  # snapshot 05: every PAGE_TYPE except promo has ≥1 page; "other" < 40% of pages
```
- [ ] **Step 2–4:** run `pytest tests/test_classify.py -v` → FAIL; implement; → PASS.
- [ ] **Step 5:** Commit `feat(classify): url/page-type authority and page dates`.

### Track C1: Rate-table extractor

**Files:** Modify `submission/kb/extract/tables.py`; Test `tests/test_extract_tables.py`
**Interfaces:** Consumes `PageContext`, `keys.make_key`, `keys.norm_*`. Produces `extract(ctx) -> list[FactObs]` with `extractor="table"`.

Header → qualifier mapping: `Term`→`term` (months), `Balance Tier|Balance|Tier`→`tier` (band via `norm_band`), `Minimum Balance|Min. to Earn|Minimum Deposit` with a bare amount cell → `tier` via `norm_band(cell, minimum=True)` (`"$100,000"`→`"100000-inf"`), `Loan Amount`→`loan_amount` (band), `Loan Program|Model Year`→`model_year`; value columns: `APR|Annual Percentage Rate`→`apr`, `APY|Annual Percentage Yield`→`apy`, `Rate|Dividend Rate|Interest Rate`→`rate`. Product from page URL/title + nearest heading (`auto` → `auto_loan`, `mortgage|home loan` + "fixed" → `mortgage_fixed`, "Jumbo" in row/heading → `kind=jumbo`, "Special" → `certificate_special`, `/business/` → `segment=business` else `segment=personal`). Tables with no value column are skipped (amortisation tables). `quote` = the table row text, ≤ 200 chars.

- [ ] **Step 1: Write failing tests**
```python
AUTO = "## Rates\n| Loan Program | Term | Annual Percentage Rate (As Low As) |\n| --- | --- | --- |\n| 2024-27 Model Year | 60 Months | 5.39% APR1 |\n| 2016 Model Year (and older) | 48 Months | 8.59% APR1 |"
def test_auto_table(): f = by_key(extract(ctx(AUTO, url="https://www.dupaco.com/personal/loans/auto-loans")))
    assert f["auto_loan|apr|model_year=2024-2027;segment=personal;term=60"].value_norm == "5.39%"
def test_footnote_suffix_stripped(): assert all(o.value_norm.endswith("%") and "APR" not in o.value_norm for o in extract(ctx(AUTO)))
def test_savings_tiers():  # "| $10,000 - $24,999 | 0.15% | 0.15% |" -> share_savings|apy|segment=personal;tier=10000-24999 == "0.15%"
def test_jumbo_special():  # "17-Month Fixed Jumbo Special | $100,000 | 4.20% APY" -> certificate_special|apy|kind=jumbo;...;term=17 == "4.20%"
def test_amortisation_table_ignored(): # "| Category | Interest | Principal | Remaining |" -> []
@pytest.mark.real
def test_real_known_values(real_snapshots):  # snapshot 05 auto page 60-month 2024-27 -> 5.39%; 30-yr fixed $100k tier rate -> 7.13%
```
- [ ] **Step 2–4:** `pytest tests/test_extract_tables.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(extract): markdown rate-table extractor`.

### Track C2: Regex extractors

**Files:** Modify `submission/kb/extract/regex.py`; Test `tests/test_extract_regex.py`
**Interfaces:** Produces `extract(ctx) -> list[FactObs]` with `extractor="regex"`.

Targets: hours (`hours_lobby`/`hours_driveup`, qualifier `day` ∈ `mon-fri|sat|sun`, value in site format e.g. "8:30 a.m. - 5:00 p.m.", "noon" kept as "noon"), address (street + city + IA/IL/WI + 5-digit ZIP, `name=<branch slug>`), phone, routing number (9 digits passing ABA checksum `3(d1+d4+d7)+7(d2+d5+d8)+(d3+d6+d9) ≡ 0 mod 10`; product `org` only when the sentence names Dupaco), counts (`org|count|what=branches`), closure dates ("will close … March 31, 2026" → `branch|closure_date`), awards ("Forbes … fifth" → `org|award|name=forbes`), program amounts (Home$tart "$15,000" → `program|detail|name=homestart`).

- [ ] **Step 1: Write failing tests**
```python
def test_routing_checksum(): assert routing(extract(ctx("Dupaco routing number: 273974581"))) == "273974581"
def test_other_institution_routing_not_org(): # "Home Savings Bank routing number 271176899" -> no org|routing_number obs
def test_hours_noon():  # "Saturday: 8:30 a.m. - noon" -> branch|hours_lobby|day=sat;name=... value "8:30 a.m. - noon"
def test_closure_date(): # "The Hy-Vee branch will close March 31, 2026" -> closure_date "March 31, 2026"
def test_branch_count(): # "22 branches" -> org|count|what=branches == "22"
```
- [ ] **Step 2–4:** `pytest tests/test_extract_regex.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(extract): regex extractors for hours, ids, counts, dates`.

### Track D: LLM prose-fact extractor and committed cache

**Files:** Modify `submission/kb/extract/llm_facts.py`; Create `submission/data/facts_cache.jsonl`, `dev/build_facts_cache.py`; Test `tests/test_extract_llm.py`
**Interfaces:** Consumes `LLM.json`, `PageContext`, `keys`. Produces `extract(ctx, llm) -> list[FactObs]` with `extractor="llm:v1"`.

Scope: only chunks of pages with `page_type in {product, branch, other, promo}` whose text matches `eligib|Home\$tart|student loan|VTO|award|mobile wallet|Apple Pay|Google Pay|Samsung Pay` — target ≤ 600 chunks total. Haiku with a JSON schema `{facts:[{product, attribute, qualifiers:{}, value}]}` whose `product`/`attribute`/qualifier names are enums from `keys`. Cache line format: `{"chunk_hash":…, "prompt_ver":"v1", "facts":[…]}` — **no quote, no page text**; at ingest, cache lookup first, `quote` re-derived from the chunk. Ingest time budget: if cumulative LLM time in this snapshot > 300 s, skip remaining calls.

- [ ] **Step 1: Write failing tests**
```python
def test_cache_hit_no_llm(tmp_cache): # chunk_hash present in cache -> FactObs returned, LLM mock not called
def test_invalid_key_dropped():       # LLM returns attribute "colour" -> dropped, no exception
def test_llm_none_returns_empty():    # LLM.json -> None -> []
def test_cache_has_no_page_text():    # every line of submission/data/facts_cache.jsonl has keys {chunk_hash,prompt_ver,facts} only
```
- [ ] **Step 2–4:** `pytest tests/test_extract_llm.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Run `python dev/build_facts_cache.py --snapshots snapshots` (all 5 snapshots, 8-way concurrency); expect ≤ 600 calls, < 5 min. Commit code + `facts_cache.jsonl`: `feat(extract): haiku prose facts with committed cache`.

### Track E: Resolver and intent selection

**Files:** Modify `submission/kb/resolve.py`; Test `tests/test_resolve.py`
**Interfaces:** Consumes `Store.facts`, `Store.live_urls`, `Intent`, `keys.band_contains`. Produces the three `resolve` signatures. `ResolvedFact.rule` ∈ `"single source"`, `"authority:<winner_type> over <loser_type>"`, `"newer page date"`, `"later observation"`, `"tie: higher authority"`.

Policy (PROJECT_PLAN §2.3), per fact key over observations in the target snapshot whose URL is live there: (1) exact key match only; (2) max authority; (3) newer `page_date`; (4) later snapshot of first observation; (5) promos with an end date before `taken_on` and news/press pages are dropped when any non-news observation exists; (6) remaining tie → sort by url, `rule="tie: higher authority"`. Losers = other observations of the key in that snapshot with a different `value_norm`.

`select_for_intent`: filter by `intent.qualifiers` (exact match; `tier`/`loan_amount`/`model_year` given as an amount or year (`"25000"`, `"2025"`) match by `band_contains` — an integer upper bound covers amounts below hi+1, so 24999.99 is in `10000-24999`); `superlative="highest"` → max value among matches; headline rules when qualifiers are under-specified: `mortgage_fixed` → `loan_amount` band containing 100000 plus term 360 and 180; `certificate_special` without `kind` → both regular and jumbo of the term; `auto_loan` without `model_year` → newest model-year band. Families: `list` returns all matches sorted by key; `fact` returns ≤ 4.

`stale_values`: every `value_norm` observed for the selected keys in any snapshot, minus the selected values.

- [ ] **Step 1: Write failing tests**
```python
def test_calculator_loses():   # rate page 5.39% vs calculator 5.34% same key, same snapshot -> value "5.39%", rule startswith "authority"
def test_dead_url_ignored():   # obs only on a url not live in the snapshot -> not resolved
def test_as_of_snapshot():     # jumbo 10-month: snapshot 02 -> "3.90%", snapshot 05 -> "4.00%"
def test_tier_boundaries():    # bands 10000-24999 / 25000-inf: amount 25000 -> 25000-inf; 24999.99 -> 10000-24999
def test_highest_tier():       # superlative highest over current tiers -> "0.20%" (not stale "0.35%" from snapshot 01)
def test_mortgage_headline():  # no loan_amount -> 100k-band 30-yr (7.13%) and 15-yr (6.25%) only
def test_stale_values():       # selected 5.39% -> stale_values contains "5.34%" and not "5.39%"
```
- [ ] **Step 2–4:** `pytest tests/test_resolve.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(resolve): authority policy, tier selection, stale sets`.

### Track F: Conflict builder

**Files:** Modify `submission/kb/conflicts.py`; Test `tests/test_conflicts.py`
**Interfaces:** Consumes `Store.fact_keys`, `Store.facts`, `Store.resolved`, `SnapshotRegistry`. Produces `build_conflicts(store, registry) -> list[Conflict]`.

One `Conflict` per fact key with ≥ 2 distinct `value_norm` across all snapshots (time changes and within-snapshot disagreements). `values` = distinct values in first-seen order; `resolution` = the key's resolved value in `registry.latest()` (or last resolved value if the key is gone in 05); `reason` = the within-05 rule if 05 had losers, else `"updated in <first snapshot id with the winning value>"`; `sources` = one `Source(url, snapshot_id, quote)` per distinct value (its highest-authority observation). `topic` = human words from product + attribute + qualifiers, e.g. `"60-month auto loan APR 2024-2027 model year personal"`, `"10-month jumbo certificate special APY"`. Precision filter: skip keys whose only disagreement comes from `extractor` starting `llm:` with a single observation, and keys of attribute `detail`. Output sorted by topic.

- [ ] **Step 1: Write failing tests**
```python
def test_one_entry_per_key():   # 3.90% (01,02), 3.95% (03), 4.00% (04,05) -> one Conflict, values ["3.90%","3.95%","4.00%"], resolution "4.00%"
def test_within_snapshot():     # 5.39% rate page vs 5.34% calculator in 05 -> resolution "5.39%", reason mentions "calculator"
def test_different_tiers_not_conflict(): # same product, tiers 10000-24999 vs 25000-inf -> []
def test_scores_against_kit_scorer(): # build on mini fixture, score_conflicts(gold, out)["f1"] == 1.0 for a 2-entry hand gold
```
- [ ] **Step 2–4:** `pytest tests/test_conflicts.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(conflicts): one conflict per fact key`.

### Track G: Hybrid retrieval and section picker

**Files:** Modify `submission/kb/retrieval/{fts,dense,pipeline,picker}.py`; Test `tests/test_retrieval.py`
**Interfaces:** Consumes `Store.fts_match`, `Store.new_chunks`, `Store.chunk_ids_in`, `LLM.json`. Produces the `Retriever` class from Task 1.3 (same four methods).
**Foundation notes:** `Store.fts_match` uses an `EXISTS` join on `chunk_snapshot` (ruling R8 — keep it; `rowid IN (subquery)` took ~25 s/query). `build()` does not clear `index_dir`, so `Retriever.__init__` must wipe any stale dense index. Chunk ids are per URL (R6), so there are more distinct chunks than the 11,777 measured; check the index-time budget early. `LLM` calls return `None` often; verify once with a live call (`@pytest.mark.live`).

- `fts.py`: `to_match_expr(question: str) -> str` (lowercase, drop stopwords and FTS syntax chars, quote tokens, OR-join, add synonyms CD↔certificate, APR↔rate); `fts_search(store, question, snapshot_id, k) -> list[Hit]`; `Bm25Fallback` over `store.chunk_ids_in(sid)` texts when `store.fts_available` is False.
- `dense.py`: Chroma persistent client at `index_dir`, one collection, one document per distinct chunk (id = `str(chunk_id)`), metadata `{"url":…, "in_<NN>": True}` per snapshot prefix (`in_01`…`in_05`), updated with `collection.update` when a chunk recurs. `index_snapshot` embeds only `store.new_chunks(sid)` plus metadata updates for the rest. Query filter `{"in_<NN>": True}`.
- `pipeline.py`: Haystack `Pipeline` with custom components `Fts5Retriever` and `ChromaRetriever` (or `chroma-haystack`'s `ChromaQueryTextRetriever` if it supports the metadata filter) → `DocumentJoiner(join_mode="reciprocal_rank_fusion")` → top k; ties broken by chunk_id. If the Haystack/Chroma path raises, `search` returns FTS-only hits.
- `picker.py`: `pick_sections(llm, question, hits) -> list[int]` — Haiku, schema `{"chunk_ids":[int], "none_relevant": bool}`, candidate texts truncated to 600 chars each; instruction: pick only sections that state the asked product's value. `Retriever.pick` maps ids back to hits (≤ 2); LLM `None` → `hits[:1]` if `hits[0].score` is the RRF top, else `[]`.

- [ ] **Step 1: Write failing tests**
```python
def test_match_expr_safe(): assert '"' not in to_match_expr('what\'s the "APR"?').replace('" OR "','')  # no syntax error in sqlite
def test_snapshot_filter(mini_store): # chunk only in 01 never returned for 05
def test_fallback_bm25(mini_store, monkeypatch): # fts_available False -> results still returned
def test_picker_none(fake_llm):  # LLM returns none_relevant -> pick() == []
def test_search_deterministic(mini_store): # two calls -> identical chunk_id lists
@pytest.mark.real
def test_real_recall(real_snapshots): # for dev/retrieval_probe.jsonl (from Track J) ≥ 22/36 have all must_include values in top 10
@pytest.mark.real
def test_real_index_time(real_snapshots): # index all 5 snapshots < 600 s
```
- [ ] **Step 2–4:** `pytest tests/test_retrieval.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(retrieval): fts5+chroma rrf pipeline with haiku picker`.

### Track H: Intent parser

**Files:** Modify `submission/kb/intent.py`; Test `tests/test_intent.py`
**Interfaces:** Consumes `keys`, `LLM.json` (fallback only, injected via `parse(question, llm=None)` keyword with default `None`). Produces `parse(question: str, llm: Optional[LLM] = None) -> Intent`.

`cleaned_question`: strip chit-chat (greetings, "I'm not asking again", "for my coffee table", sentences without a product/attribute/question word). Synonym tables: CD/certificate/share certificate → `certificate`; "special" → `certificate_special`; APR → `apr`, APY/yield → `apy`, "interest rate"/"dividend" → `rate`; "jumbo" → `kind=jumbo`; "business" → `segment=business` else `personal`; "N-month"/"N year" → `term`; "$X" or "between $X and $Y" → `tier` amount (lower bound); "2025 model year"/"2025 vehicle" → `model_year="2025"` (the year itself; Track E matches it against site bands with `band_contains` — ruling R13); "highest/best" → `superlative="highest"`; "all/list/which … do you offer" → family `list`; hours/address/phone/routing/how many branches → fact family with branch/org product and `name` slug. `canonical = make_key(product or "-", attribute or "-", qualifiers) + "|" + family + "|" + (superlative or "")`; prose family canonical = sorted content words. Rules confidence low (no product found) and `llm` given → Haiku closed-label classification, cached.

- [ ] **Step 1: Write failing tests**
```python
def test_auto(): i = parse("What's the APR for a 60-month auto loan on a 2025 model-year vehicle?")
    assert (i.product, i.attribute, i.qualifiers.get("term"), i.qualifiers.get("model_year")) == ("auto_loan","apr","60","2024-2027")
def test_paraphrases_same_canonical():
    assert parse("What is the APY on Dupaco's 17-month special certificate?").canonical == parse("17 month CD special — what's the yield?").canonical
def test_chitchat_stripped(): assert "coffee table" not in parse("For my coffee table book: what's the routing number?").cleaned_question
def test_highest(): assert parse("What's the highest share savings rate?").superlative == "highest"
def test_list_family(): assert parse("Which credit cards do you offer and their APRs?").family == "list"
def test_unknown_is_prose(): assert parse("Tell me about your community impact").family == "prose"
```
- [ ] **Step 2–4:** `pytest tests/test_intent.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(intent): rule-based intent parser with haiku fallback`.

### Track I: Composer, templates, sanitizer, refusal

**Files:** Modify `submission/kb/compose.py`; Test `tests/test_compose.py`
**Interfaces:** Consumes `ResolvedFact`, `Hit`, `Intent`, `LLM.text`, contract `Answer`/`Source`. Produces the four `compose` signatures.

- `render_facts`: one template per attribute family, using the site's formatting: `"The 60-month APR for 2024-2027 model-year vehicles is as low as 5.39% APR."`; list family = one line per fact; sources = winner urls (dedup, order kept) with `snapshot_id` and `quote`; `conflict_note` = `"Other pages show <loser values>; used <winner page_type> page (<rule>)."` when any selected fact has losers — note goes only in `conflict_note`, never in `text`.
- `render_prose`: Sonnet via `LLM.text`, system prompt: answer only from the given sections, current values only, no history, no "facts/context" wording, never ask a question, ≤ 3 sentences. LLM `None` → extractive: the sentences of the top hit that share most content words with the question, ≤ 400 chars.
- `refusal(question)`: `Answer(text="That isn't listed on the website, so I can't confirm it. Please contact Dupaco directly for this information.", confident=False)`.
- `sanitize`: drop any sentence of `text` containing a number in `drop` and none in `keep` (compare with `scoring._numbers` semantics: value + unit); drop sentences matching `in these facts|context|would you like`.

- [ ] **Step 1: Write failing tests**
```python
from scoring import contains, REFUSAL_RE, _numbers
def test_template_matches_scorer(): a = render_facts(q, intent, [rf("5.39%")], "05_…"); assert contains(a.text, "5.39%") and a.sources
def test_conflict_note_not_in_text(): a = render_facts(..., [rf("5.39%", losers=["5.34%"])]); assert "5.34" not in a.text and "5.34" in a.conflict_note
def test_refusal_has_no_digits(): a = refusal("What is the 7/1 ARM rate?"); assert not a.confident and REFUSAL_RE.search(a.text) and not _numbers(a.text)
def test_sanitize_drops_stale(): assert "5.34" not in sanitize(Answer("Now 5.39%. Was 5.34%."), {"5.39%"}, {"5.34%"}).text
def test_prose_fallback_without_llm(): # llm disabled -> extractive text from hit, source = hit.url
def test_no_question_back(): # composed prose text has no "?" 
```
- [ ] **Step 2–4:** `pytest tests/test_compose.py -v` FAIL → implement → PASS.
- [ ] **Step 5:** Commit `feat(compose): templates, prose composer, sanitizer, refusal`.

### Track J: Discovery scripts and dev eval fixtures

**Files:** Create `dev/discover.py`, `dev/topic_trace.md`, `dev/conflicts_gold.json`, `dev/paraphrase_eval.jsonl`, `dev/asof_eval.jsonl`, `dev/refusal_eval.jsonl`, `dev/retrieval_probe.jsonl`, `dev/score_run.sh`; Test `tests/test_dev_fixtures.py`
**Interfaces:** Produces fixtures in the kit's `eval/public_eval.jsonl` item format (`id, question, as_of, expected_answer, must_include, must_not_include, category, difficulty, weight, expected_sources, group`) and conflicts gold in `harness/selftest/conflicts_gold.json` format (`id, topic_keywords, values, correct_value`).

- `discover.py`: per-snapshot URL added/removed/changed (by cleaned-text hash once Track A lands; raw hash before), path histogram.
- `topic_trace.md`: values across 01→05 for 60/84-month auto APR, 30/15-yr mortgage, 10/17-month specials (regular/jumbo), share-savings tiers, Hy-Vee closure, lobby/drive-up hours, routing number, branch count, Forbes ranking; and for each of the 40 public items, the answer page and the trap page.
- `conflicts_gold.json`: ~25 entries from the trace, both time changes and within-snapshot disagreements.
- `paraphrase_eval.jsonl`: one rewording per public item, same `group` id as the original (groups of 2).
- `asof_eval.jsonl`: 6 changing facts × (each snapshot date, one between-date, one before 2026-05-12, one after 2026-09-28).
- `refusal_eval.jsonl`: 7/1 ARM, construction loan rate, Mullan Avenue lobby hours + 10 invented products, all `category="should_refuse"`; plus 10 answerable look-alikes that must not refuse.
- `score_run.sh <label>`: runs `run_eval.py` on public (with `--conflicts-gold dev/conflicts_gold.json --judge`) and on the concatenated dev sets, appends a row to `dev/scores.csv`.

- [ ] **Step 1: Write failing test** `test_fixtures_valid`: every jsonl line has all eval keys; every gold entry has ≥ 2 `values` and a `correct_value` among them; no fixture question string equals a public question.
- [ ] **Step 2:** `pytest tests/test_dev_fixtures.py -v` → FAIL (files missing).
- [ ] **Step 3:** Write scripts and fixtures (hand-checked against the snapshots).
- [ ] **Step 4:** → PASS.
- [ ] **Step 5:** Commit `chore(dev): discovery scripts and dev eval fixtures`.

---

## Stage 3 — Integration (sequential)

### Task 3.1: Retrieval path → minimum deliverable

**Needs:** A, B, G, I merged (plus J's `score_run.sh` if available).
- [ ] **Step 1:** Merge the four track branches into `main`; `pytest -v` → PASS.
- [ ] **Step 2:** `dev/score_run.sh min` (or the raw `run_eval.py` command). Expected: public ≥ 0.35, p95 < 1 s with `KB_NO_LLM=1`, p95 < 2 s with LLM on, 0 errors/timeouts.
- [ ] **Step 3:** Read every miss in `results/min/`; fix only in the owning module, each fix with a test in that module's test file.
- [ ] **Step 4:** Commit; `git tag min-deliverable`; push.

### Task 3.2: Fact path

**Needs:** C1, C2, D, E, H merged.
- [ ] **Step 1:** Merge; `pytest -v` → PASS.
- [ ] **Step 2:** `dev/score_run.sh facts`. Expected: public ≥ 0.65, `stale_trap_hits == 0`; refusal fixture: refuse ≥ 95% of unanswerable, false refusals ≤ 5%; as_of fixture 100%.
- [ ] **Step 3:** Paraphrase fixture consistency ≥ 0.9; repeat consistency 1.00. Fix misses in owning modules with tests.
- [ ] **Step 3b (ruling R15):** anchor `snapshots.parse_question_date` — accept dates only after "as of", "as it stood", "back in", or "in <Month YYYY>" not preceded by an event verb; add negative tests: "Is the Hy-Vee branch closing March 31, 2026?" → None; "Do may 2026 graduates qualify?" → None; "What did the May 2026 press release say?" → None.
- [ ] **Step 4:** Commit `feat: fact path integrated`; push.

### Task 3.3: Conflicts tuning

**Needs:** F, J merged.
- [ ] **Step 1:** `python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl --conflicts-gold dev/conflicts_gold.json`. Expected: conflicts F1 ≥ 0.6, resolution accuracy ≥ 0.9.
- [ ] **Step 2:** Print 30 random emitted conflicts (`dev/sample_conflicts.py`); mark each true/false in `dev/conflict_audit.md`. Expected: precision ≥ 80%. Tighten Track F filters with tests if lower.
- [ ] **Step 3:** Commit `tune(conflicts): precision filters`.

### Task 3.4: Hardening and submission

**Files:** Create `README.md`, `submission/NOTES.md`, `dev/clean_env_check.sh`
- [ ] **Step 1:** `NOTES.md`: approach, authority policy (§2.3 verbatim order), as_of rule, refusal rule and chunk-dedup decision (Task 1.3), one-conflict-per-fact decision with rationale and trade-off (PROJECT_PLAN §9.5), `ANTHROPIC_API_KEY` required, paid API disclosure (Anthropic: Haiku 5.5, Sonnet 5.5), `KB_NO_LLM=1` fallback.
- [ ] **Step 2:** `README.md`: Python 3.11 venv, `pip install -r submission/requirements.txt`, put the five zips in `snapshots_zip/` at repo root (unpacked automatically), `export ANTHROPIC_API_KEY=...`, the exact eval command `python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl`.
- [ ] **Step 3:** `dev/clean_env_check.sh`: fresh `git clone` into a temp dir → new 3.11 venv → install → copy zips → run eval with `--judge` → print ingest time and p95. Expected: clean run, ingest < 10 min cold / < 2 min warm, p95 ≤ 1.5 s, max < 10 s.
- [ ] **Step 4:** `grep -rnE "requests|urllib|httpx|aiohttp" submission/` → only matches inside comments or none; `git ls-files | grep -E "snapshots/0|\.zip$"` → empty; solution folder is `submission/`.
- [ ] **Step 5:** Commit `docs: README and NOTES`; `git tag submission`; push with tags; record `git rev-parse submission` in `dev/scores.csv` and send repo URL + hash to organizers.

---

## Cut list (only if ahead of schedule; merge only if public and held-out scores don't drop)

Promo-expiry date reasoning beyond Track E rule (5), "explain" CLI, an Opus-generated large paraphrase set, faster embedding model swap (only if `test_real_index_time` fails — then it becomes required).
