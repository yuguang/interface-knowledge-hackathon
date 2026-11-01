import json
from pathlib import Path

import pytest

from kb.extract import llm_facts
from kb.model import Chunk, PageContext, PageInfo, StoredChunk, chunk_hash

URL = "https://www.dupaco.com/x"
TEXT = ("Dupaco supports Apple Pay and Google Pay. Members need a checking account to be eligible for "
        "the Home$tart program.")


def _ctx(text=TEXT, page_type="product", sid="s1"):
    ch = Chunk(chunk_hash(URL, text), URL, "T", "H", text)
    return PageContext(sid, "2026-05-12", URL, "T", PageInfo(page_type, 80, None), text, [StoredChunk(7, ch)])


class FakeLLM:
    def __init__(self, out):
        self.out, self.calls = out, 0

    def json(self, *a, **k):
        self.calls += 1
        return self.out


GOOD = {"facts": [{"product": "checking", "attribute": "detail", "qualifiers": {"what": "mobile wallet"},
                   "value": "Apple Pay and Google Pay"}]}


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_facts, "_cache", None)
    monkeypatch.setattr(llm_facts, "_spent", {})
    monkeypatch.setattr(llm_facts, "CACHE_PATH", tmp_path / "missing.jsonl")


def test_llm_fact_with_rederived_quote():
    llm = FakeLLM(GOOD)
    [o] = llm_facts.extract(_ctx(), llm)
    assert o.extractor == "llm:v1" and o.chunk_id == 7
    assert o.fact_key == "checking|detail|segment=personal;what=mobile wallet"  # segment defaulted (F10)
    assert o.quote == "Dupaco supports Apple Pay and Google Pay." and len(o.quote) <= 200


def test_cache_hit_no_llm(tmp_path, monkeypatch):
    p = tmp_path / "c.jsonl"
    ctx = _ctx()
    p.write_text(json.dumps({"chunk_hash": ctx.chunks[0].chunk.hash, "prompt_ver": "v1",
                             "facts": GOOD["facts"]}) + "\n")
    monkeypatch.setattr(llm_facts, "CACHE_PATH", p)
    llm = FakeLLM(None)
    assert len(llm_facts.extract(ctx, llm)) == 1
    assert llm.calls == 0


def test_invalid_key_dropped():
    bad = {"facts": [{"product": "checking", "attribute": "colour", "qualifiers": {}, "value": "Apple Pay"},
                     {"product": "checking", "attribute": "detail", "qualifiers": {"zzz": "1"}, "value": "Apple Pay"}]}
    assert llm_facts.extract(_ctx(), FakeLLM(bad)) == []


def test_llm_none_returns_empty():
    assert llm_facts.extract(_ctx(), FakeLLM(None)) == []


def test_value_not_in_chunk_dropped():
    out = {"facts": [{"product": "checking", "attribute": "detail", "qualifiers": {}, "value": "Venmo"}]}
    assert llm_facts.extract(_ctx(), FakeLLM(out)) == []


def test_out_of_scope_not_called():
    llm = FakeLLM(GOOD)
    assert llm_facts.extract(_ctx(page_type="blog"), llm) == []
    assert llm_facts.extract(_ctx(text="Nothing relevant here."), llm) == []
    assert llm.calls == 0


def test_time_budget_skips_remaining(monkeypatch):
    llm = FakeLLM(GOOD)
    monkeypatch.setattr(llm_facts, "_spent", {"s1": llm_facts.BUDGET_S + 1})
    assert llm_facts.extract(_ctx(sid="s1"), llm) == []
    assert llm.calls == 0
    assert len(llm_facts.extract(_ctx(sid="s2"), llm)) == 1  # other snapshot unaffected


def test_cache_has_no_page_text():
    p = Path(__file__).resolve().parent.parent / "submission" / "data" / "facts_cache.jsonl"
    for line in p.read_text().splitlines():
        assert set(json.loads(line)) == {"chunk_hash", "prompt_ver", "facts"}


def test_builder_dry_run(capsys):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"))
    import build_facts_cache as b
    assert b.main(["--snapshots", str(Path(__file__).parent / "fixtures" / "mini_site"), "--dry-run"]) == 0
    assert "in-scope chunks:" in capsys.readouterr().out


# ---- Stage 2 final fix wave

def _one(facts, text=TEXT):
    return llm_facts.extract(_ctx(text), FakeLLM({"facts": facts}))


def test_qualifiers_normalised_like_table_and_regex_keys():
    text = ("Members are eligible: the Platinum Visa offers 9.90% APR for 60 months on balances of $1,000 to $4,999 "
            "for 2023-26 models.")
    [o] = _one([{"product": "credit_card", "attribute": "apr", "value": "9.90%",
                 "qualifiers": {"name": "Platinum Visa", "term": "60 months", "tier": "$1,000 to $4,999"}}], text)
    assert o.fact_key == "credit_card|apr|name=platinum-visa;segment=personal;term=60;tier=1000-4999"
    assert o.qualifiers == {"name": "platinum-visa", "segment": "personal", "term": "60", "tier": "1000-4999"}
    [o] = _one([{"product": "auto_loan", "attribute": "apr", "value": "9.90%",
                 "qualifiers": {"model_year": "2023-26", "loan_amount": "$1,000+", "segment": "Business"}}], text)
    assert o.fact_key == "auto_loan|apr|loan_amount=1000-inf;model_year=2023-2026;segment=business"


def test_segment_default_only_for_rate_products_and_unparseable_dropped():
    [o] = _one([{"product": "org", "attribute": "eligibility", "qualifiers": {}, "value": "checking account"}])
    assert o.fact_key == "org|eligibility|"
    assert _one([{"product": "certificate", "attribute": "apy", "value": "Apple Pay",
                  "qualifiers": {"term": "a while"}}]) == []
    [o] = _one([{"product": "checking", "attribute": "detail", "qualifiers": {"term": "12"}, "value": "Apple Pay"}])
    assert o.qualifiers == {"segment": "personal", "term": "12"}


def test_ask_normalises_before_caching():
    llm = FakeLLM({"facts": [{"product": "branch", "attribute": "hours_lobby", "qualifiers": {"name": "Hy Vee"},
                              "value": "9 a.m."}]})
    assert llm_facts.ask(llm, "x")[0]["qualifiers"] == {"name": "hy-vee"}


def test_system_prompt_spells_formats():
    for word in ("hyphen", "months", "2023-2026", "personal"):
        assert word in llm_facts.SYSTEM
    assert llm_facts.PROMPT_VER == "v1"
