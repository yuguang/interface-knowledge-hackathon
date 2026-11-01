import time
from types import SimpleNamespace

from kb.llm import HAIKU, LLM


def _fake_client(create):
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _text_resp(t):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=t)])


def test_disabled_returns_none(tmp_path):
    llm = LLM(tmp_path / "c.sqlite", enabled=False)
    assert llm.text(HAIKU, "s", "u") is None
    assert llm.json(HAIKU, "s", "u", {"type": "object"}) is None


def test_llm_failure_returns_none(tmp_path):
    def boom(**kw):
        raise RuntimeError("down")
    llm = LLM(tmp_path / "c.sqlite", enabled=True)
    llm._client = _fake_client(boom)
    assert llm.text(HAIKU, "s", "u") is None
    assert llm.json(HAIKU, "s", "u", {"type": "object"}) is None


def test_slow_call_times_out(tmp_path):
    def slow(**kw):
        time.sleep(1.0)
        return _text_resp("late")
    llm = LLM(tmp_path / "c.sqlite", enabled=True)
    llm._client = _fake_client(slow)
    t0 = time.time()
    assert llm.text(HAIKU, "s", "u", timeout_s=0.1) is None
    assert time.time() - t0 < 0.9


def test_cache_hit_skips_call(tmp_path):
    calls = []

    def create(**kw):
        calls.append(kw)
        return _text_resp("hello")
    llm = LLM(tmp_path / "c.sqlite", enabled=True)
    llm._client = _fake_client(create)
    assert llm.text(HAIKU, "s", "u") == "hello"
    assert llm.text(HAIKU, "s", "u") == "hello"
    assert len(calls) == 1 and calls[0]["temperature"] == 0
    # persisted across instances
    llm2 = LLM(tmp_path / "c.sqlite", enabled=True)
    llm2._client = _fake_client(lambda **kw: (_ for _ in ()).throw(AssertionError("called")))
    assert llm2.text(HAIKU, "s", "u") == "hello"


def test_json_uses_forced_tool(tmp_path):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"a": 1})])
    llm = LLM(tmp_path / "c.sqlite", enabled=True)
    llm._client = _fake_client(create)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    assert llm.json(HAIKU, "s", "u", schema) == {"a": 1}
    assert seen["tools"][0]["input_schema"] == schema
    assert seen["tool_choice"] == {"type": "tool", "name": seen["tools"][0]["name"]}
    assert llm.json(HAIKU, "s", "u", schema) == {"a": 1}


def test_env_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("KB_NO_LLM", "1")
    assert LLM(tmp_path / "c.sqlite").enabled is False
