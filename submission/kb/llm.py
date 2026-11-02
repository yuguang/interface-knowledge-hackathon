"""Fail-soft, cached, deterministic Claude wrapper. Never raises: any problem yields None.

Request shape is pinned to the installed SDK (anthropic 2.3.0, API version 2026-10-21):
- no sampling params (`temperature` etc. are rejected on this API version);
- `json()` uses structured outputs, `output_config={"format": {"type": "json_schema", "schema": ...}}`,
  and parses the returned text block (forced `tool_choice` is a 400 on Sonnet 5.5);
- `output_config.effort = "low"` keeps thinking short (thinking can't be disabled on these models and
  counts toward `max_tokens`);
- a response whose `stop_reason` is not a normal completion is a failure: None, never cached.
"""
from __future__ import annotations

import collections
import concurrent.futures as cf
import contextvars
import copy
import hashlib
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from . import tracing

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"

EFFORT = "low"
_OK_STOPS = frozenset({"end_turn", "stop_sequence"})
log = logging.getLogger("kb")


class _Fail(Exception):
    """Internal: a counted failure with a reason key."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _api_schema(schema: dict) -> dict:
    """The schema as the API expects it (additionalProperties: false etc.), via the SDK's own transform."""
    try:
        from anthropic.lib._parse._transform import transform_schema
        return transform_schema(copy.deepcopy(schema))
    except Exception:
        return schema


class LLM:
    def __init__(self, cache_path: Path, enabled: Optional[bool] = None):
        if enabled is None:
            enabled = bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("KB_NO_LLM") != "1"
        self.enabled = enabled
        self.failures: collections.Counter = collections.Counter()
        self._client = None  # built lazily, so a disabled LLM never imports the SDK
        self._lock = threading.Lock()
        self._db = None
        try:
            path = Path(cache_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS llm_cache (k TEXT PRIMARY KEY, v TEXT)")
            self._db.commit()
        except Exception:
            self._db = None

    # cache
    @staticmethod
    def _key(model: str, system: str, user: str, schema: Optional[dict]) -> str:
        blob = json.dumps([model, system, user, schema], sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _get(self, k: str):
        if self._db is None:
            return None
        with self._lock:
            row = self._db.execute("SELECT v FROM llm_cache WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else None

    def _put(self, k: str, v) -> None:
        if self._db is None:
            return
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?)", (k, json.dumps([v])))
            self._db.commit()

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = tracing.wrap_anthropic(anthropic.Anthropic(max_retries=0))
        return self._client

    def _record(self, reason: str, model: str, detail: str) -> None:
        self.failures[reason] += 1
        tracing.annotate(llm_failure=reason)
        tracing.mark_failed(f"llm {reason}: {detail}"[:300])
        log.warning("llm %s failed (%s): %s", model, reason, detail[:200])

    @staticmethod
    def _request(model: str, system: str, user: str, max_tokens: int, schema: Optional[dict]) -> dict:
        """The exact kwargs passed to `Messages.create` (besides `timeout`)."""
        output_config: dict = {"effort": EFFORT}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": _api_schema(schema)}
        return dict(model=model, system=system, max_tokens=max_tokens,
                    messages=[{"role": "user", "content": user}], output_config=output_config)

    def _call(self, timeout_s: float, **kw):
        """Run messages.create with both the SDK timeout and a wall-clock guard; return the text output."""
        ex = cf.ThreadPoolExecutor(max_workers=1)
        try:
            # copy_context: the worker thread must see the current trace span, or the llm run becomes a root
            ctx = contextvars.copy_context()
            fut = ex.submit(ctx.run, lambda: self._get_client().messages.create(timeout=timeout_s, **kw))
            try:
                resp = fut.result(timeout=timeout_s + 0.2)
            except cf.TimeoutError:
                raise _Fail("timeout", f"no response within {timeout_s}s")
            except Exception as e:
                if type(e).__name__ == "APITimeoutError":
                    raise _Fail("timeout", f"{type(e).__name__}: {e}")
                raise _Fail("exception", f"{type(e).__name__}: {e}")
        finally:
            ex.shutdown(wait=False)
        stop = getattr(resp, "stop_reason", None)
        if stop not in _OK_STOPS:
            raise _Fail("truncated", f"stop_reason={stop}")
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    @tracing.traced("kb.llm", "chain", inputs=lambda d: {k: d[k] for k in ("model", "system", "user", "schema")
                                                         if k in d})
    def _run(self, model: str, system: str, user: str, max_tokens: int, timeout_s: float,
             schema: Optional[dict]):
        if not self.enabled:
            tracing.annotate(llm_enabled=False)
            return None
        try:
            key = self._key(model, system, user, schema)
            hit = self._get(key)
            if hit:
                tracing.annotate(cache_hit=True)
                return hit[0]
            tracing.annotate(cache_hit=False)
            out = self._call(timeout_s, **self._request(model, system, user, max_tokens, schema))
            if schema is not None:
                try:
                    out = json.loads(out)
                except ValueError as e:
                    raise _Fail("parse", f"invalid JSON: {e}")
                if not isinstance(out, dict):
                    raise _Fail("parse", f"expected an object, got {type(out).__name__}")
            elif not out:
                raise _Fail("parse", "empty text")
            self._put(key, out)
            return out
        except _Fail as f:
            self._record(f.reason, model, f.detail)
        except Exception as e:
            self._record("exception", model, f"{type(e).__name__}: {e}")
        return None

    def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int = 2048,
             timeout_s: float = 8.0) -> Optional[dict]:
        return self._run(model, system, user, max_tokens, timeout_s, schema)

    def text(self, model: str, system: str, user: str, max_tokens: int = 1024,
             timeout_s: float = 8.0) -> Optional[str]:
        return self._run(model, system, user, max_tokens, timeout_s, None)
