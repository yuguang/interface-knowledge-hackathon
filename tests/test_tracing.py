"""LangSmith tracing: masking, no-op safety, and the span tree. No network: runs are captured in memory."""
from unittest.mock import MagicMock

import pytest

from kb import tracing

ls = pytest.importorskip("langsmith")


def test_scrub_masks_secrets_and_pii_but_keeps_business_facts():
    s = tracing.scrub_text(
        "key sk-ant-api03-abcdefghijkl mail me@example.com ssn 123-45-6789 card 4111 1111 1111 1111 "
        "call 319-555-0100 rate 5.25% dates 2026-05-12 2026-09-28")
    assert "sk-ant" not in s and "me@example.com" not in s and "123-45-6789" not in s and "4111" not in s
    assert "319-555-0100" in s and "5.25%" in s and "2026-05-12 2026-09-28" in s  # not Luhn-valid: kept


def test_scrub_redacts_secret_keys_in_dicts():
    assert tracing.scrub({"api_key": "x", "q": "hi"}) == {"api_key": "[REDACTED]", "q": "hi"}


def test_summarize_bounds_size_and_hides_objects():
    out = tracing.summarize({"text": "a" * 5000, "items": list(range(100)), "store": object()})
    assert len(out["text"]) < 1600 and len(out["items"]) == tracing.MAX_ITEMS + 1 and out["store"] == "<object>"


def test_traced_is_transparent_when_tracing_is_off(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)

    @tracing.traced("t", "tool")
    def f(x, y=2):
        return x + y

    assert f(1) == 3 and f.__name__ == "f"
    tracing.annotate(a=1)  # no current run: must not raise
    with tracing.span("s") as run:
        run.end(outputs={"ok": True})


def test_span_tree_and_error_status():
    client = ls.Client(api_url="http://localhost:1", api_key="k", session=MagicMock(), auto_batch_tracing=False)
    runs = {}

    def grab(name):
        runs[name] = tracing._current_run()

    @tracing.traced("child.tool", "tool")
    def child(q):
        grab("child")
        tracing.annotate(step_number=1)
        return {"hit": q}

    @tracing.traced("root.chain", "chain")
    def root(q):
        grab("root")
        child(q)
        with tracing.span("loop.step", metadata={"step_number": 2}):
            grab("loop")
        raise ValueError("boom")

    with ls.tracing_context(enabled=True, client=client):
        with pytest.raises(ValueError):
            root("hello me@example.com")
    assert runs["child"].parent_run_id == runs["root"].id and runs["loop"].parent_run_id == runs["root"].id
    assert runs["child"].run_type == "tool" and runs["child"].metadata["step_number"] == 1
    assert runs["loop"].metadata["step_number"] == 2
    assert "boom" in (runs["root"].error or "")
    assert "me@example.com" not in str(runs["root"].inputs)
