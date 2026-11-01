import pytest
from contract import Answer

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


def test_stage_exception_yields_refusal(monkeypatch):
    ks = _ks()

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(intent, "parse", boom)
    a = ks.answer(Q)
    assert a.confident is False and not a.sources


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

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(intent, "parse", boom)
    with caplog.at_level("ERROR", logger="kb"):
        a = ks.answer(Q)
    assert a.confident is False and ks.stage_errors["answer"] == 1
    assert any("RuntimeError" in r.getMessage() for r in caplog.records)
