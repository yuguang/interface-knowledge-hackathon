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


# ---- certificate scope through answer generation (Task 2)

def _cert_ks():
    """A system ingested (at store level) with regular + special certificates: May and latest snapshots."""
    from kb import resolve
    from kb.keys import parse_key
    from kb.model import AUTHORITY, FactObs, PageInfo
    ks = build()
    snaps = {"01_2026-05-12_a": "2026-05-12", "05_2026-09-28_latest": "2026-09-28"}
    url = "https://www.dupaco.com/rates/certificates"
    for sid, d in snaps.items():
        ks.registry.add(sid, d)
        ks.store.add_snapshot(sid, d)
        ks.store.add_page(sid, url, "t", PageInfo("rate_sheet", AUTHORITY["rate_sheet"], None), "h")
        rows = []
        for t in (3, 6, 12, 24, 36, 60):
            rows += [(f"certificate|apy|term={t}", f"{t}.01%"), (f"certificate|rate|term={t}", f"{t}.00%")]
        for t in (10, 17):
            jumbo = ("7.90%" if sid.startswith("01") else "7.95%") if t == 10 else "5.55%"
            rows += [(f"certificate_special|apy|term={t};tier=1000-99999.99", f"{t}.51%"),
                     (f"certificate_special|apy|kind=jumbo;term={t};tier=100000-inf", jumbo)]
        obs = []
        for key, val in rows:
            p, a, q = parse_key(key)
            obs.append(FactObs(key, p, a, q, val, val, sid, d, url, "rate_sheet", AUTHORITY["rate_sheet"], None,
                               f"{key} {val}", "table"))
        ks.store.add_facts(obs)
        ks.store.set_resolved(sid, resolve.resolve_snapshot(ks.store, sid))
    return ks


def test_generic_certificate_answer_includes_regular_and_specials():
    ks = _cert_ks()
    a = ks.answer("What are your certificate rates?")
    assert a.confident and "?" not in a.text
    lines = [l for l in a.text.splitlines() if l.strip()]
    assert len(lines) > 4
    for v in ("3.01%", "60.01%", "10.51%", "17.51%", "5.55%", "7.95%"):
        assert v in a.text
    assert "7.90%" not in a.text


def test_regular_certificate_answer_excludes_specials():
    ks = _cert_ks()
    a = ks.answer("What are the regular certificate rates?")
    assert "3.01%" in a.text and "60.01%" in a.text
    assert "10.51%" not in a.text and "7.95%" not in a.text and "5.55%" not in a.text


def test_certificate_list_keeps_rate_and_apy_pairs():
    ks = _cert_ks()
    a = ks.answer("What are your certificate rates?")
    for t in (3, 6, 12, 24, 36, 60):
        assert any(f"{t}.01%" in l and f"{t}.00%" in l for l in a.text.splitlines()), t
    assert {s.snapshot_id for s in a.sources} == {"05_2026-09-28_latest"}
    assert all(s.url == "https://www.dupaco.com/rates/certificates" for s in a.sources)


def test_certificate_as_of_never_mixes_jumbo_special_values():
    ks = _cert_ks()
    may = ks.answer("What are your certificate rates?", as_of="2026-05-15")
    assert "7.90%" in may.text and "7.95%" not in may.text
    assert {s.snapshot_id for s in may.sources} == {"01_2026-05-12_a"}
    latest = ks.answer("What are your certificate rates?")
    assert "7.95%" in latest.text and "7.90%" not in latest.text


# ---- VTO reporting-year routing (Task 4)

VTO_URL = "https://www.dupaco.com/about/community"
VTO_SID = "05_2026-09-28_latest"


def _vto_ks():
    from kb import resolve
    from kb.keys import parse_key
    from kb.model import AUTHORITY, FactObs, PageInfo
    counts = {"2023": "294", "2024": "256"}
    ks = build()
    d = "2026-09-28"
    ks.registry.add(VTO_SID, d)
    ks.store.add_snapshot(VTO_SID, d)
    ks.store.add_page(VTO_SID, VTO_URL, "t", PageInfo("other", AUTHORITY["other"], None), "h")
    obs = []
    for y, c in counts.items():
        key = f"org|count|what=vto-employees;year={y}"
        p, a, q = parse_key(key)
        obs.append(FactObs(key, p, a, q, c, c, VTO_SID, d, VTO_URL, "other", AUTHORITY["other"], None,
                           f"{c} Dupaco employees used VTO in {y}", "regex"))
    ks.store.add_facts(obs)
    ks.store.set_resolved(VTO_SID, resolve.resolve_snapshot(ks.store, VTO_SID))
    return ks


def _spy_retrieval(monkeypatch):
    from kb.retrieval.pipeline import Retriever
    calls = []
    monkeypatch.setattr(Retriever, "search", lambda self, *a, **k: calls.append(a) or [])
    return calls


@pytest.mark.parametrize("q,year,count", [
    ("How many Dupaco employees used Volunteer Time Off in 2023?", "2023", "294"),
    ("How many Dupaco employees used Volunteer Time Off in 2024?", "2024", "256"),
    ("How many Dupaco employees used VTO?", "2024", "256"),
])
def test_vto_count_selects_requested_or_latest_year(q, year, count, monkeypatch):
    ks = _vto_ks()
    calls = _spy_retrieval(monkeypatch)
    a = ks.answer(q)
    assert a.confident and a.text == f"In {year}, {count} Dupaco employees used Volunteer Time Off."
    assert not calls and a.sources and a.sources[0].url == VTO_URL


def test_vto_missing_requested_year_refuses_without_retrieval(monkeypatch):
    ks = _vto_ks()
    calls = _spy_retrieval(monkeypatch)
    for q in ("How many Dupaco employees used Volunteer Time Off last year?",
              "How many Dupaco employees used Volunteer Time Off in 2022?"):
        a = ks.answer(q)
        assert a.confident is False and not a.sources and "294" not in a.text and "256" not in a.text
    assert not calls and not ks.stage_errors


def test_vto_undated_without_vto_facts_refuses_without_retrieval(monkeypatch):
    from kb import resolve
    from kb.keys import parse_key
    from kb.model import AUTHORITY, FactObs, PageInfo
    ks = build()
    d = "2026-09-28"
    ks.registry.add(VTO_SID, d)
    ks.store.add_snapshot(VTO_SID, d)
    ks.store.add_page(VTO_SID, VTO_URL, "t", PageInfo("other", AUTHORITY["other"], None), "h")
    key = "org|count|what=branches"
    p, a, q = parse_key(key)
    ks.store.add_facts([FactObs(key, p, a, q, "13", "13", VTO_SID, d, VTO_URL, "other", AUTHORITY["other"], None,
                                "13 branches; employees volunteered 3,400 hours", "regex")])
    ks.store.set_resolved(VTO_SID, resolve.resolve_snapshot(ks.store, VTO_SID))
    calls = _spy_retrieval(monkeypatch)
    a = ks.answer("How many employees used VTO?")
    assert a.confident is False and not a.sources and not any(c.isdigit() for c in a.text)
    assert not calls and not ks.stage_errors


def test_vto_last_year_is_relative_to_reference_date():
    ks = _vto_ks()
    assert "In 2023, 294" in ks.answer("How many employees used VTO last year?", as_of="2024-12-31").text
    assert "In 2024, 256" in ks.answer("How many employees used VTO last year?", as_of="2025-12-31").text


def test_vto_memo_is_keyed_by_reporting_year():
    ks = _vto_ks()
    q = "How many employees used VTO last year?"
    a23 = ks.answer(q, as_of="2024-12-31")
    a24 = ks.answer(q, as_of="2025-12-31")
    assert "294" in a23.text and "256" in a24.text
    assert "294" in ks.store.memo_get("org|count|what=vto-employees;year=2023|fact|", VTO_SID)["text"]
    assert "256" in ks.store.memo_get("org|count|what=vto-employees;year=2024|fact|", VTO_SID)["text"]
    assert "294" in ks.answer(q, as_of="2024-12-31").text and "256" in ks.answer(q, as_of="2025-12-31").text


def test_vto_trace_fields(monkeypatch):
    from kb import tracing
    seen = {}
    monkeypatch.setattr(tracing, "annotate", lambda **kw: seen.update(kw))
    _vto_ks().answer("How many employees used VTO last year?", as_of="2024-12-31")
    assert seen["reference_date"] == "2024-12-31" and seen["reporting_year"] == "2023"


@pytest.mark.parametrize("q", ["What are the certificate APYs?", "CD APYs"])
def test_apy_worded_certificate_request_lists_regular_and_specials(q):
    ks = _cert_ks()
    a = ks.answer(q)
    lines = [l for l in a.text.splitlines() if l.strip()]
    assert len(lines) > 4
    for v in ("3.01%", "60.01%", "10.51%", "17.51%", "5.55%", "7.95%"):
        assert v in a.text


def test_regular_and_special_request_includes_both(monkeypatch):
    ks = _cert_ks()
    a = ks.answer("Show me certificate rates and specials")
    for v in ("3.01%", "60.01%", "10.51%", "7.95%"):
        assert v in a.text


def test_vto_took_last_year_refuses_without_retrieval(monkeypatch):
    ks = _vto_ks()
    calls = _spy_retrieval(monkeypatch)
    a = ks.answer("How many Dupaco employees took Volunteer Time Off last year?")
    assert a.confident is False and not a.sources and "294" not in a.text and "256" not in a.text
    assert not calls and not ks.stage_errors
    ok = ks.answer("How many Dupaco employees took Volunteer Time Off in 2024?")
    assert ok.confident and "256" in ok.text
