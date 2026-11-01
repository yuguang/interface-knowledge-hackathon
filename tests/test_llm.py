import inspect
import logging
import os
import time
from types import SimpleNamespace

import pytest

from kb.llm import HAIKU, LLM, SONNET


def _fake_client(create):
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _text_resp(t, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=t)],
                           stop_reason=stop)


def _llm(tmp_path, create):
    llm = LLM(tmp_path / "c.sqlite", enabled=True)
    llm._client = _fake_client(create)
    return llm


SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


def test_disabled_returns_none(tmp_path):
    llm = LLM(tmp_path / "c.sqlite", enabled=False)
    assert llm.text(HAIKU, "s", "u") is None
    assert llm.json(HAIKU, "s", "u", {"type": "object"}) is None
    assert not llm.failures  # disabled is not a failure


def test_llm_failure_returns_none(tmp_path, caplog):
    def boom(**kw):
        raise RuntimeError("down")
    llm = _llm(tmp_path, boom)
    with caplog.at_level("WARNING", logger="kb"):
        assert llm.text(HAIKU, "s", "u") is None
        assert llm.json(HAIKU, "s", "u", {"type": "object"}) is None
    assert llm.failures["exception"] == 2
    assert sum("exception" in r.getMessage() for r in caplog.records) == 2


def test_slow_call_times_out(tmp_path, caplog):
    def slow(**kw):
        time.sleep(1.0)
        return _text_resp("late")
    llm = _llm(tmp_path, slow)
    t0 = time.time()
    with caplog.at_level("WARNING", logger="kb"):
        assert llm.text(HAIKU, "s", "u", timeout_s=0.1) is None
    assert time.time() - t0 < 0.9
    assert llm.failures["timeout"] == 1
    recs = [r for r in caplog.records if r.name == "kb"]
    assert len(recs) == 1 and recs[0].exc_info is None and "Traceback" not in recs[0].getMessage()


def test_cache_hit_skips_call(tmp_path):
    calls = []

    def create(**kw):
        calls.append(kw)
        return _text_resp("hello")
    llm = _llm(tmp_path, create)
    assert llm.text(HAIKU, "s", "u") == "hello"
    assert llm.text(HAIKU, "s", "u") == "hello"
    assert len(calls) == 1 and "temperature" not in calls[0]
    assert calls[0]["max_tokens"] == 1024 and calls[0]["output_config"] == {"effort": "low"}
    # persisted across instances
    llm2 = _llm(tmp_path, lambda **kw: (_ for _ in ()).throw(AssertionError("called")))
    assert llm2.text(HAIKU, "s", "u") == "hello"


def test_json_uses_structured_output(tmp_path):
    seen = []

    def create(**kw):
        seen.append(kw)
        return _text_resp('{"a": 1}')
    llm = _llm(tmp_path, create)
    assert llm.json(HAIKU, "s", "u", SCHEMA) == {"a": 1}
    kw = seen[0]
    assert "tools" not in kw and "tool_choice" not in kw and "temperature" not in kw
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["properties"] == SCHEMA["properties"]
    assert fmt["schema"]["additionalProperties"] is False  # SDK transform applied
    assert "additionalProperties" not in SCHEMA  # caller's schema not mutated
    assert kw["output_config"]["effort"] == "low" and kw["max_tokens"] == 2048
    assert llm.json(HAIKU, "s", "u", SCHEMA) == {"a": 1} and len(seen) == 1  # cached


def test_call_kwargs_match_installed_sdk(tmp_path):
    """Contract: every kwarg passed to messages.create is a parameter of the installed SDK's Messages.create."""
    from anthropic.resources.messages import Messages
    from anthropic.types.json_output_format_param import JSONOutputFormatParam
    from anthropic.types.output_config_param import OutputConfigParam
    sig = inspect.signature(Messages.create)
    seen = []

    def create(**kw):
        seen.append(kw)
        return _text_resp('{"a": 1}')
    llm = _llm(tmp_path, create)
    llm.json(HAIKU, "s", "u", SCHEMA)
    llm.text(SONNET, "s", "u")
    assert len(seen) == 2
    for kw in seen:
        sig.bind(None, **kw)  # TypeError on any unknown kwarg / missing required one
        assert set(kw["output_config"]) <= set(OutputConfigParam.__annotations__)
    assert set(seen[0]["output_config"]["format"]) <= set(JSONOutputFormatParam.__annotations__)


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "pause_turn", None])
def test_abnormal_stop_is_none_and_not_cached(tmp_path, stop):
    calls = []

    def create(**kw):
        calls.append(kw)
        return _text_resp('{"a": 1}' if len(calls) > 2 else '{"a": ', stop=stop if len(calls) <= 2 else "end_turn")
    llm = _llm(tmp_path, create)
    assert llm.text(HAIKU, "s", "u") is None
    assert llm.json(HAIKU, "s", "u", SCHEMA) is None
    assert llm.failures["truncated"] == 2
    # nothing cached: the next calls hit the client again and succeed
    assert llm.json(HAIKU, "s", "u", SCHEMA) == {"a": 1}
    assert llm.text(HAIKU, "s", "u") == '{"a": 1}'
    assert len(calls) == 4


def test_json_parse_failure_counted(tmp_path):
    llm = _llm(tmp_path, lambda **kw: _text_resp("not json"))
    assert llm.json(HAIKU, "s", "u", SCHEMA) is None
    assert llm.failures["parse"] == 1


def test_env_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("KB_NO_LLM", "1")
    assert LLM(tmp_path / "c.sqlite").enabled is False


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_live_smoke(tmp_path, caplog):
    llm = LLM(tmp_path / "c.sqlite", enabled=True)
    schema = {"type": "object", "properties": {"capital": {"type": "string"}, "n": {"type": "integer"}},
              "required": ["capital", "n"]}
    with caplog.at_level(logging.WARNING, logger="kb"):
        t0 = time.time()
        out = llm.json(HAIKU, "Answer with the requested fields.",
                       "What is the capital of France, and what is 2+3 (as n)?", schema, timeout_s=30)
        t1 = time.time()
        txt = llm.text(SONNET, "Reply in one short sentence.", "Say hello.", timeout_s=30)
        t2 = time.time()
    print(f"\njson -> {out!r} ({t1 - t0:.2f}s)\ntext -> {txt!r} ({t2 - t1:.2f}s)\nfailures={dict(llm.failures)}")
    for r in caplog.records:
        print("log:", r.getMessage())
    assert isinstance(out, dict) and set(out) == {"capital", "n"}
    assert isinstance(out["capital"], str) and isinstance(out["n"], int)
    assert isinstance(txt, str) and txt.strip()
