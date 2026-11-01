"""Fail-soft, cached, deterministic Claude wrapper. Never raises: any problem yields None."""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"

_TOOL = "emit"


class LLM:
    def __init__(self, cache_path: Path, enabled: Optional[bool] = None):
        if enabled is None:
            enabled = bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("KB_NO_LLM") != "1"
        self.enabled = enabled
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
            self._client = anthropic.Anthropic(max_retries=0)
        return self._client

    def _call(self, timeout_s: float, **kw):
        """Run messages.create with both the SDK timeout and a wall-clock guard."""
        ex = cf.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(lambda: self._get_client().messages.create(timeout=timeout_s, temperature=0, **kw))
            return fut.result(timeout=timeout_s + 0.2)
        finally:
            ex.shutdown(wait=False)

    def _cached(self, key: str):
        hit = self._get(key)
        return (True, hit[0]) if hit else (False, None)

    def json(self, model: str, system: str, user: str, schema: dict, max_tokens: int = 512,
             timeout_s: float = 8.0) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            key = self._key(model, system, user, schema)
            found, val = self._cached(key)
            if found:
                return val
            resp = self._call(
                timeout_s, model=model, system=system, max_tokens=max_tokens,
                messages=[{"role": "user", "content": user}],
                tools=[{"name": _TOOL, "description": "Return the structured result.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": _TOOL})
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    out = dict(block.input)
                    self._put(key, out)
                    return out
            return None
        except Exception:
            return None

    def text(self, model: str, system: str, user: str, max_tokens: int = 400,
             timeout_s: float = 8.0) -> Optional[str]:
        if not self.enabled:
            return None
        try:
            key = self._key(model, system, user, None)
            found, val = self._cached(key)
            if found:
                return val
            resp = self._call(timeout_s, model=model, system=system, max_tokens=max_tokens,
                              messages=[{"role": "user", "content": user}])
            out = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            if not out:
                return None
            self._put(key, out)
            return out
        except Exception:
            return None
