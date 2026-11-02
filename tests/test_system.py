import pytest
from contract import Answer
from scoring import contains

from conftest import mini_snapshots as _mini
from kb import intent
from solution import build

Q = "What is the APR for a 60-month auto loan?"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_DB_PATH", str(tmp_path / "sub" / "kb.sqlite"))
    monkeypatch.setenv("KB_LLM_CACHE", str(tmp_path / "llm.sqlite"))
    monkeypatch.setenv("KB_NO_LLM", "1")


def _ks():
    ks = build()
    for s in _mini():
        ks.ingest(s)
    return ks


def test_end_to_end_mini():
    ks = _ks()
    a = ks.answer(Q)
    assert isinstance(a, Answer) and a.sources
    b = ks.answer(Q, as_of="2026-05-15")
    assert all(s.snapshot_id.startswith("01") for s in b.sources)
    assert isinstance(ks.conflicts(), list)


def test_deterministic():
    ks = _ks()
    assert ks.answer(Q).text == ks.answer(Q).text


def test_build_resets_db(tmp_path):
    _ks()
    ks2 = build()
    assert ks2.store.fact_keys() == [] and ks2.store.live_urls("x") == set()


def _boom(*a, **k):
    raise RuntimeError("x")


def test_unguarded_failure_yields_refusal(monkeypatch):
    ks = _ks()
    monkeypatch.setattr(ks.registry, "resolve", _boom)  # outside any per-stage guard -> outer catch-all
    a = ks.answer(Q)
    assert a.confident is False and not a.sources and ks.stage_errors["answer"] == 1


def test_extractor_failure_keeps_other_facts(monkeypatch):
    from kb.extract import llm_facts, tables
    from kb.model import FactObs

    def one_fact(ctx):
        return [FactObs("auto_loan|apr|-", "auto_loan", "apr", {}, "5%", "5.00%", ctx.snapshot_id, ctx.taken_on,
                        ctx.url, "other", 40, None, "q", "table")] if ctx.url.endswith("/") else []

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(tables, "extract", one_fact)
    monkeypatch.setattr(llm_facts, "extract", boom)
    ks = _ks()
    assert ks.store.fact_keys()
    assert ks.stage_errors["llm_facts"] > 0 and not ks.stage_errors["tables"]


def test_answer_failure_is_logged(monkeypatch, caplog):
    ks = _ks()
    monkeypatch.setattr(ks.registry, "resolve", _boom)
    with caplog.at_level("ERROR", logger="kb"):
        a = ks.answer(Q)
    assert a.confident is False and ks.stage_errors["answer"] == 1
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)


# --- F3 (R14): per-stage degradation ---

def test_classify_failure_page_still_chunked_and_searchable(monkeypatch):
    from kb import classify
    monkeypatch.setattr(classify, "classify_page", _boom)
    ks = _ks()
    assert ks.stage_errors["classify"] > 0
    a = ks.answer(Q)
    assert a.sources


def test_clean_and_chunk_failure_fall_back(monkeypatch):
    from kb import normalize
    monkeypatch.setattr(normalize, "fit_boilerplate", _boom)
    monkeypatch.setattr(normalize, "clean", _boom)
    monkeypatch.setattr(normalize, "chunk_page", _boom)
    ks = _ks()
    assert ks.stage_errors["fit_boilerplate"] == len(_mini()) and ks.stage_errors["clean"] > 0 and ks.stage_errors["chunk"] > 0
    assert ks.answer(Q).sources


def test_resolve_and_index_failure_answer_still_has_sources(monkeypatch):
    from kb import resolve
    from kb.retrieval.pipeline import Retriever
    monkeypatch.setattr(Retriever, "index_snapshot", _boom)
    monkeypatch.setattr(resolve, "resolve_snapshot", _boom)
    ks = _ks()
    assert ks.stage_errors["index_snapshot"] == ks.stage_errors["resolve"] == len(_mini())
    assert ks.answer(Q).sources


def test_intent_failure_uses_retrieval_not_refusal(monkeypatch):
    ks = _ks()
    monkeypatch.setattr(intent, "parse", _boom)
    a = ks.answer(Q)
    assert a.sources and ks.stage_errors["intent"] == 1 and not ks.stage_errors["answer"]


def _fact_ks(monkeypatch):
    """A system whose intent selects a resolved fact, so the facts path runs."""
    from kb import resolve
    from kb.model import FactObs, Intent, ResolvedFact
    ks = _ks()
    obs = FactObs("auto_loan|apr|term=60", "auto_loan", "apr", {"term": "60"}, "5.39%", "5.39%",
                  "05_x", "2026-09-28", "https://example.com/rates", "rate_sheet", 100, None, "60 mo 5.39%", "table")
    rf = ResolvedFact(obs.fact_key, "5.39%", obs, [], "authority")
    monkeypatch.setattr(intent, "parse", lambda q, llm=None: Intent("fact", "auto_loan", "apr", {"term": "60"}, None,
                                                                   q, "auto_loan|apr|term=60"))
    monkeypatch.setattr(resolve, "select_for_intent", lambda it, res: [rf])
    return ks


def test_render_facts_failure_falls_through_to_retrieval(monkeypatch):
    from kb import compose
    ks = _fact_ks(monkeypatch)
    monkeypatch.setattr(compose, "render_facts", _boom)
    a = ks.answer(Q)
    assert a.sources and ks.stage_errors["render_facts"] == 1


def test_select_failure_uses_retrieval(monkeypatch):
    from kb import resolve
    ks = _fact_ks(monkeypatch)
    monkeypatch.setattr(resolve, "select_for_intent", _boom)
    a = ks.answer(Q)
    assert a.sources and ks.stage_errors["select"] == 1


def test_retrieval_failure_is_refusal(monkeypatch):
    from kb.retrieval.pipeline import Retriever
    ks = _ks()
    monkeypatch.setattr(Retriever, "search", _boom)
    a = ks.answer(Q)
    assert a.confident is False and not a.sources and ks.stage_errors["retrieval"] == 1


def test_sanitize_failure_empty_drop_keeps_answer(monkeypatch):
    from kb import compose
    ks = _ks()
    monkeypatch.setattr(compose, "sanitize", _boom)
    a = ks.answer(Q)
    assert a.sources and ks.stage_errors["sanitize"] == 1


def test_sanitize_failure_with_stale_values_refuses(monkeypatch):
    from kb import compose, resolve
    ks = _fact_ks(monkeypatch)
    monkeypatch.setattr(resolve, "stale_values", lambda store, sel: {"4.99%"})
    monkeypatch.setattr(compose, "sanitize", _boom)
    a = ks.answer(Q)
    assert a.confident is False and not a.sources


def test_stale_values_failure_is_empty_set(monkeypatch):
    from kb import compose, resolve
    ks = _fact_ks(monkeypatch)
    seen = {}
    monkeypatch.setattr(resolve, "stale_values", _boom)

    def spy(ans, keep, drop):
        seen["drop"] = drop
        return ans
    monkeypatch.setattr(compose, "sanitize", spy)
    a = ks.answer(Q)
    assert seen["drop"] == set() and contains(a.text, "5.39%") and ks.stage_errors["stale_values"] == 1


def test_degraded_answer_not_memoised(monkeypatch):
    ks = _ks()
    monkeypatch.setattr(intent, "parse", _boom)
    ks.answer(Q)
    assert ks.store.memo_get(Q.lower().strip(), ks.registry.resolve(None)) is None


def test_conflicts_failure_is_empty_and_not_cached(monkeypatch):
    from kb import conflicts as conflicts_mod
    ks = _ks()
    monkeypatch.setattr(conflicts_mod, "build_conflicts", _boom)
    assert ks.conflicts() == [] and ks.stage_errors["conflicts"] == 1
    monkeypatch.setattr(conflicts_mod, "build_conflicts", lambda store, reg: ["c"])
    assert ks.conflicts() == ["c"]


# ---- Stage 2 final fix wave

def test_prose_path_drops_stale_values_of_intent_candidates(monkeypatch):
    """F8: the intent names a product but no fact matches its qualifiers, so the prose path answers; values that
    are stale for that product/attribute are still stripped (drop is not empty)."""
    from contract import Answer as A
    from kb import compose, resolve
    from kb.model import FactObs, Intent, ResolvedFact
    ks = _ks()
    obs = FactObs("auto_loan|apr|term=60", "auto_loan", "apr", {"term": "60"}, "5.39%", "5.39%",
                  "05_x", "2026-09-28", "https://example.com/rates", "rate_sheet", 100, None, "60 mo 5.39%", "table")
    other = FactObs("auto_loan|min_balance|", "auto_loan", "min_balance", {}, "$5", "$5",
                    "05_x", "2026-09-28", "https://example.com/rates", "rate_sheet", 100, None, "$5", "table")
    rf, rf_other = ResolvedFact(obs.fact_key, "5.39%", obs, [], "single source"), \
        ResolvedFact(other.fact_key, "$5", other, [], "single source")
    monkeypatch.setattr(intent, "parse", lambda q, llm=None: Intent("fact", "auto_loan", None, {"term": "999"}, None,
                                                                   q, "auto_loan|rate|term=999|fact||generic"))
    monkeypatch.setattr(ks.store, "resolved", lambda sid, product=None, attribute=None: [rf, rf_other])
    seen = {}

    def stale(store, selected):
        seen["keys"] = [r.fact_key for r in selected]
        return {"4.99%"}
    monkeypatch.setattr(resolve, "stale_values", stale)
    monkeypatch.setattr(compose, "render_prose", lambda *a, **k: A(text="Auto loans were 4.99% APR. Apply online today."))
    a = ks.answer(Q)
    assert seen["keys"] == ["auto_loan|apr|term=60"]  # generic rate: rate/APR candidates only, qualifiers ignored
    assert "4.99" not in a.text and "Apply online" in a.text


def test_fact_path_drop_still_uses_selected(monkeypatch):
    from kb import resolve
    ks = _fact_ks(monkeypatch)
    seen = {}

    def stale(store, selected):
        seen["keys"] = [r.fact_key for r in selected]
        return set()
    monkeypatch.setattr(resolve, "stale_values", stale)
    ks.answer(Q)
    assert seen["keys"] == ["auto_loan|apr|term=60"]


# ---- Stage 3 Task 3.1

def test_prose_answer_gets_link_targets_of_picked_pages(monkeypatch):
    """Ingest keeps each page's link targets (clean() drops them); the prose composer gets those of picked pages."""
    from contract import Page, Snapshot
    from kb import compose
    ks = build()
    url = "https://www.dupaco.com/about/join"
    md = "# Join\n\nReady to join Dupaco online?\n\n[Become a member](https://join.dupaco.com/)\n"
    other = [Page(f"https://www.dupaco.com/p{i}", f"P{i}", f"# P{i}\n\nPage {i} text.\n", "2026-09-28T00:00:00Z")
             for i in range(5)]  # one page alone would make every line boilerplate
    ks.ingest(Snapshot("05_2026-09-28_latest", "2026-09-28", [Page(url, "Join", md, "2026-09-28T00:00:00Z")] + other))
    seen = {}

    def prose(question, it, hits, sid, llm, links=None, **kw):
        seen["links"] = links
        return Answer(text="Use the Become a member button.")
    monkeypatch.setattr(compose, "render_prose", prose)
    monkeypatch.setattr(ks.retriever, "pick", lambda q, hits: hits[:1])
    ks.answer("How do I join Dupaco online?")
    assert seen["links"] == {url: [("Become a member", "https://join.dupaco.com/")]}
