"""LangSmith tracing: optional, fail-soft, and masked.

Tracing is a no-op unless `LANGSMITH_TRACING=true` (plus `LANGSMITH_API_KEY`) is set, and it is never allowed
to change an answer: if `langsmith` is not installed, or anything in here raises, the wrapped code runs untraced.

Span layout (run types follow the LangSmith convention: chain = orchestration, tool = a deterministic step,
llm = a model call, which `wrap_anthropic` creates):

    kb.answer (chain)                      <- root; metadata: env, session_id, as_of, path, degraded
      ├─ kb.intent (chain)
      │    └─ ChatAnthropic (llm)          <- only when the rules need the LLM labeller
      ├─ kb.select_facts (tool)
      ├─ kb.render_facts (tool)            <- fact path
      ├─ kb.search (tool) -> kb.pick (chain) -> ChatAnthropic (llm)  <- retrieval path
      ├─ kb.render_prose (chain)
      ├─ kb.stale_values (tool)            <- guardrail
      └─ kb.sanitize (tool)                <- guardrail
    kb.ingest (chain) └─ kb.ingest_page (chain, step_number/of) └─ ChatAnthropic (llm)

Everything sent to LangSmith goes through `scrub()` first (API keys, e-mail addresses, SSNs, card numbers), and
large objects are summarised, so a trace never carries page markdown or a whole snapshot.
"""
from __future__ import annotations

import contextlib
import dataclasses
import functools
import logging
import os
import re
import uuid
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger("kb")

try:  # the submission must work without langsmith installed
    from langsmith import trace as _ls_trace
    from langsmith import traceable as _ls_traceable
    from langsmith.run_helpers import get_current_run_tree as _current_run
    _HAVE_LS = True
except Exception:  # pragma: no cover - exercised only when the package is missing
    _HAVE_LS = False

if os.environ.get("LANGSMITH_TRACING", "").lower() == "true" and not (
        os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
    os.environ["LANGSMITH_TRACING"] = "false"  # tracing on but no key: stay off instead of failing every upload

ENV = os.environ.get("KB_ENV") or "dev"
SESSION_ID = os.environ.get("KB_SESSION_ID") or uuid.uuid4().hex
USER_ID = os.environ.get("KB_USER_ID")  # optional; set it for per-tenant filtering
BASE_TAGS = ["interface-kb", f"env:{ENV}"]

MAX_STR = 1500
MAX_ITEMS = 20

# --- masking -----------------------------------------------------------------------------------------------

_SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|password|passwd|authorization|credential)", re.I)
_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"lsv2_[A-Za-z0-9_]{8,}"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _mask_card(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return "[CARD]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)


def scrub_text(s: str) -> str:
    """Mask secrets and PII in a string. Business facts (phone numbers, rates, addresses) are kept on purpose."""
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return _CARD.sub(_mask_card, s)


def scrub(obj: Any, _depth: int = 0) -> Any:
    """Recursively scrub strings in dicts/lists/tuples; other objects are returned unchanged."""
    if isinstance(obj, str):
        return scrub_text(obj)
    if _depth > 8:
        return obj
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if isinstance(k, str) and _SECRET_KEY.search(k) else scrub(v, _depth + 1))
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v, _depth + 1) for v in obj]
    return obj


def summarize(v: Any, _depth: int = 0) -> Any:
    """A bounded, JSON-friendly, scrubbed view of `v`. Unknown objects (Store, LLM, ...) become their type name."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        s = scrub_text(v)
        return s if len(s) <= MAX_STR else s[:MAX_STR] + f"... [{len(s) - MAX_STR} more chars]"
    if _depth > 4:
        return f"<{type(v).__name__}>"
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: summarize(getattr(v, f.name), _depth + 1) for f in dataclasses.fields(v)}
    if isinstance(v, dict):
        items = list(v.items())[:MAX_ITEMS]
        out = {str(k): ("[REDACTED]" if _SECRET_KEY.search(str(k)) else summarize(x, _depth + 1))
               for k, x in items}
        if len(v) > MAX_ITEMS:
            out["..."] = f"{len(v) - MAX_ITEMS} more"
        return out
    if isinstance(v, (list, tuple, set, frozenset)):
        seq = list(v)
        out = [summarize(x, _depth + 1) for x in seq[:MAX_ITEMS]]
        if len(seq) > MAX_ITEMS:
            out.append(f"... {len(seq) - MAX_ITEMS} more")
        return out
    return f"<{type(v).__name__}>"


def _default_inputs(inputs: dict) -> dict:
    return {k: summarize(v) for k, v in inputs.items() if k not in ("self", "cls")}


def _default_outputs(out: Any) -> dict:
    s = summarize(out)
    return s if isinstance(s, dict) else {"output": s}


# --- spans -------------------------------------------------------------------------------------------------

def enabled() -> bool:
    """True when a trace started now would be sent to LangSmith."""
    if not _HAVE_LS:
        return False
    try:
        from langsmith.utils import tracing_is_enabled
        return bool(tracing_is_enabled())
    except Exception:
        return False


def traced(name: str, run_type: str = "chain", *, inputs: Optional[Callable[[dict], dict]] = None,
           outputs: Optional[Callable[[Any], dict]] = None, tags: Optional[list[str]] = None,
           metadata: Optional[dict] = None) -> Callable:
    """Decorator: run the function inside a LangSmith span. Identity decorator if langsmith is unavailable.

    Exceptions propagate unchanged, so the span is marked failed with its stack trace. `inputs`/`outputs` map
    the call's named arguments / return value to what is logged; the defaults summarise and scrub.
    """
    if not _HAVE_LS:
        return lambda fn: fn
    meta = {"env": ENV, "session_id": SESSION_ID, **({"user_id": USER_ID} if USER_ID else {}), **(metadata or {})}
    in_fn = (lambda d: scrub(inputs(d))) if inputs else _default_inputs
    out_fn = (lambda o: scrub(outputs(o))) if outputs else _default_outputs

    def deco(fn: Callable) -> Callable:
        return functools.wraps(fn)(_ls_traceable(
            name=name, run_type=run_type, tags=BASE_TAGS + (tags or []), metadata=meta,
            process_inputs=in_fn, process_outputs=out_fn)(fn))
    return deco


class _NullRun:
    """Stands in for a RunTree when tracing is off, so callers never branch."""
    metadata: dict = {}

    def end(self, **_kw) -> None:
        pass


@contextlib.contextmanager
def span(name: str, run_type: str = "chain", *, inputs: Optional[dict] = None,
         metadata: Optional[dict] = None, tags: Optional[list[str]] = None) -> Iterator[Any]:
    """Context-manager span for code that is not a function of its own (e.g. one iteration of a loop).

    Yields a run with `.end(outputs=...)` and `.metadata`. An exception inside marks the span failed and
    propagates. If the tracing layer itself breaks, the body still runs untraced.
    """
    if not _HAVE_LS or not enabled():
        yield _NullRun()
        return
    try:
        cm = _ls_trace(name, run_type=run_type, inputs=summarize(inputs or {}), tags=BASE_TAGS + (tags or []),
                       metadata=scrub({"env": ENV, "session_id": SESSION_ID, **(metadata or {})}))
        run = cm.__enter__()
    except Exception:
        log.debug("tracing: could not open span %s", name, exc_info=True)
        yield _NullRun()
        return
    try:
        yield _SafeRun(run)
    except BaseException as e:
        try:
            cm.__exit__(type(e), e, e.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            log.debug("tracing: could not close span %s", name, exc_info=True)


class _SafeRun:
    def __init__(self, run: Any):
        self._run = run
        self.metadata = getattr(run, "metadata", {})

    def end(self, **kw) -> None:
        try:
            if "outputs" in kw:
                kw["outputs"] = _default_outputs(kw["outputs"])
            self._run.end(**kw)
        except Exception:
            pass


def annotate(**metadata: Any) -> None:
    """Add metadata to the span currently executing (for example `path="facts"` or `degraded=True`)."""
    if not _HAVE_LS:
        return
    try:
        rt = _current_run()
        if rt is not None:
            rt.metadata.update(scrub(metadata))
    except Exception:
        pass


def mark_failed(message: str) -> None:
    """Mark the current span as failed without raising, for fail-soft code that swallows its own errors
    (a degraded stage should still show up red in the dashboard)."""
    if not _HAVE_LS:
        return
    try:
        rt = _current_run()
        if rt is not None and not rt.error:
            rt.error = scrub_text(str(message))[:500]
    except Exception:
        pass


def tag(*tags: str) -> None:
    """Add tags to the current span."""
    if not _HAVE_LS:
        return
    try:
        rt = _current_run()
        if rt is not None:
            rt.tags.extend(t for t in tags if t not in rt.tags)
    except Exception:
        pass


def current_run_id() -> Optional[str]:
    """Id of the span currently executing; pair with `feedback()` to attach an eval score."""
    if not _HAVE_LS:
        return None
    try:
        rt = _current_run()
        return str(rt.id) if rt is not None else None
    except Exception:
        return None


# --- Anthropic client, feedback, flush ---------------------------------------------------------------------

_client = None


def get_client():
    """One LangSmith client that masks inputs and outputs before they leave the process."""
    global _client
    if _client is None:
        from langsmith import Client
        _client = Client(hide_inputs=lambda d: scrub(d), hide_outputs=lambda d: scrub(d))
    return _client


def wrap_anthropic(client: Any) -> Any:
    """Trace every `messages.create` (tokens, latency, model) as an llm child span; the client is returned as is
    when tracing is off or the wrapper cannot be applied."""
    if not enabled():
        return client
    try:
        from langsmith.wrappers import wrap_anthropic as _wrap
        return _wrap(client, tracing_extra={"client": get_client(), "tags": BASE_TAGS,
                                            "metadata": {"env": ENV, "session_id": SESSION_ID}})
    except Exception:
        log.warning("tracing: could not wrap the Anthropic client; LLM calls will not be traced", exc_info=True)
        return client


def _init() -> None:
    """Route every span (ours and the Anthropic wrapper's) through the one masking client."""
    if not enabled():
        return
    try:
        from langsmith import run_trees
        run_trees.configure(client=get_client())
    except Exception:
        log.warning("tracing: could not install the masking client", exc_info=True)


_init()


def feedback(run_id: str, key: str, score: Optional[float] = None, comment: Optional[str] = None) -> None:
    """Attach an evaluation score (grounding, correctness, user rating...) to a trace. Never raises."""
    if not _HAVE_LS or not run_id or not enabled():
        return
    try:
        get_client().create_feedback(run_id, key, score=score, comment=comment and scrub_text(comment))
    except Exception:
        log.debug("tracing: feedback failed", exc_info=True)


def flush() -> None:
    """Block until queued traces are sent. Call before a short-lived process exits."""
    if not _HAVE_LS:
        return
    try:
        get_client().flush()
    except Exception:
        pass
