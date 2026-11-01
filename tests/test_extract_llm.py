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
    assert o.fact_key == "checking|detail|what=mobile wallet"
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
