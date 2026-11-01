"""Answer composition: fact templates, prose composer, refusal, and the stale-value sanitizer."""
from __future__ import annotations

import re

from contract import Answer, Source
from scoring import _numbers

from .keys import parse_key
from .llm import SONNET, LLM
from .model import Hit, Intent, ResolvedFact

_REFUSAL = "That isn't listed on the website, so I can't confirm it. Please contact Dupaco directly for this information."

_PRODUCT_LABEL = {
    "auto_loan": "auto loan", "mortgage_fixed": "fixed-rate mortgage", "mortgage_arm": "adjustable-rate mortgage",
    "heloc": "HELOC", "home_equity_loan": "home equity loan", "personal_loan": "personal loan",
    "student_loan": "student loan", "construction_loan": "construction loan", "rv_loan": "RV loan",
    "certificate": "certificate", "certificate_special": "special certificate", "share_savings": "share savings account",
    "money_market": "money market account", "checking": "checking account", "bright_savings": "Bright Savings account",
    "jump_start": "Jump Start account", "ira": "IRA", "credit_card": "credit card", "branch": "branch",
    "org": "Dupaco", "program": "program",
}
_BANNED = re.compile(r"in these facts|context|would you like", re.I)
_SPLIT = re.compile(r"(?<=[.!?])\s+")
_AMPM_END = re.compile(r"\b[ap]\.m\.$", re.I)
_ABBR_END = re.compile(r"\b(?:St|Ave|Blvd|Rd|Dr|Ste|Mt|Ft|Mr|Mrs|Ms|Jr|Sr|Hwy)\.$")
_DAY_START = re.compile(r"(?:mon|tue|wed|thu|fri|sat|sun)", re.I)


def _continues(prev: str, nxt: str) -> bool:
    """True when the period ending `prev` is an abbreviation dot, not a sentence end."""
    if _AMPM_END.search(prev):
        return bool(nxt) and (nxt[0] in "-\u2013\u2014" or nxt[0].isdigit() or nxt[0].islower()
                              or bool(_DAY_START.match(nxt)))
    return bool(_ABBR_END.search(prev)) and bool(nxt) and (nxt[0].isupper() or nxt[0].isdigit())


def _sentences(text: str) -> list[str]:
    """Split into sentences without breaking inside abbreviations like "a.m." or "St."."""
    out: list[str] = []
    for part in _SPLIT.split(text):
        if out and _continues(out[-1], part):
            out[-1] += " " + part
        else:
            out.append(part)
    return out
_STOP = frozenset("a an and are as at be by can do does for from has have how i in is it its me my of on or our "
                  "that the their there this to was what when where which who will with you your dupaco".split())


def _term_phrase(t: str) -> str:
    return f"{t}-month" if t.isdigit() else t


def _subject_parts(key: str) -> tuple[str, str, str, dict]:
    """(prefix adjectives, product label, trailing 'for ...' phrase, qualifiers)."""
    product, attribute, q = parse_key(key)
    pre, post = [], []
    if "term" in q:
        pre.append(_term_phrase(q["term"]))
    if "segment" in q and q["segment"] not in ("", "all", "personal"):
        pre.append(q["segment"])
    if "kind" in q:
        pre.append(q["kind"])
    label = _PRODUCT_LABEL.get(product, product.replace("_", " "))
    if "model_year" in q:
        post.append(f"{q['model_year']} model-year vehicles")
    if "tier" in q:
        post.append(f"the {q['tier']} tier")
    if "loan_amount" in q:
        post.append(f"loan amounts of {q['loan_amount']}")
    if "name" in q:
        post.append(q["name"])
    if "what" in q:
        post.append(q["what"])
    if "day" in q:
        post.append(q["day"])
    return " ".join(pre), label, " and ".join(post), q


def _line(fact: ResolvedFact) -> str:
    """One sentence for one fact, using the site's wording (e.g. "as low as 5.39% APR")."""
    product, attribute, _ = parse_key(fact.fact_key)
    pre, label, post, q = _subject_parts(fact.fact_key)
    v = fact.value
    if attribute in ("apr", "apy"):
        unit = attribute.upper()
        if product == "auto_loan":
            prefix = f"The {pre + ' ' if pre else ''}{unit}"
            if "model_year" in q:
                return f"{prefix} for {q['model_year']} model-year vehicles is as low as {v} {unit}."
            return f"{prefix} for an auto loan is as low as {v} {unit}."
        who = f"{pre + ' ' if pre else ''}{label}"
        verb = "as low as" if attribute == "apr" else "up to"
        extra = f" for {post}" if post else ""
        return f"The {who} {unit}{extra} is {verb} {v} {unit}."
    what = {"rate": "rate", "min_balance": "minimum balance", "hours_lobby": "lobby hours",
            "hours_driveup": "drive-up hours", "address": "address", "phone": "phone number", "status": "status",
            "closure_date": "closure date", "routing_number": "routing number", "count": "count", "url": "web address",
            "award": "award", "eligibility": "eligibility", "detail": "detail"}.get(attribute, attribute.replace("_", " "))
    who = f"{pre + ' ' if pre else ''}{label}"
    if product == "branch" and "name" in q:
        who, post = f"{q['name']} branch", ""
    extra = f" for {post}" if post else ""
    return f"The {who} {what}{extra} is {v}."


def render_facts(question: str, intent: Intent, selected: list[ResolvedFact], snapshot_id: str) -> Answer:
    text = "\n".join(_line(r) for r in selected)
    sources, seen = [], set()
    for r in selected:
        if r.winner.url not in seen:
            seen.add(r.winner.url)
            sources.append(Source(r.winner.url, r.winner.snapshot_id, r.winner.quote))
    notes = []
    for r in selected:
        others = list(dict.fromkeys(o.value_norm for o in r.losers if o.value_norm != r.value))
        if others:
            notes.append(f"Other pages show {', '.join(others)}; used {r.winner.page_type} page ({r.rule}).")
    return Answer(text=text, sources=sources, conflict_note=" ".join(notes) or None)


_PROSE_SYSTEM = (
    "You answer a credit-union member's question using only the sections of the Dupaco website provided. "
    "State current values only, with no history of earlier values. Never mention 'facts' or 'context'. "
    "Never ask the member a question. Answer in at most 3 sentences. If the sections do not answer the question, "
    "reply with exactly: NONE"
)


def _clean_prose(text: str) -> str:
    """Drop questions and banned-phrase sentences from model output."""
    sents = [s for s in _sentences(text.strip()) if s and not s.rstrip().endswith("?") and not _BANNED.search(s)]
    return " ".join(sents).strip()


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP}


def _extractive(question: str, text: str, limit: int = 400) -> str:
    sents = [s.strip() for s in _sentences(" ".join(text.split())) if s.strip()
             and not s.rstrip().endswith("?") and not _BANNED.search(s)]
    if not sents:
        return ""
    qw = _words(question)
    scores = [len(qw & _words(s)) for s in sents]
    best = max(scores)
    order = sorted(range(len(sents)), key=lambda i: (-scores[i], i))
    chosen = [order[0]] if best == 0 else [i for i in order if scores[i] > 0][:2]
    out, used = "", []
    for i in sorted(chosen):
        cand = (out + " " + sents[i]).strip()
        if len(cand) <= limit:
            out = cand
            used.append(i)
    return out or sents[order[0]][:limit]


def render_prose(question: str, intent: Intent, hits: list[Hit], snapshot_id: str, llm: LLM) -> Answer:
    top = hits[0]
    sections = "\n\n".join(f"[{h.heading_path}]\n{h.text}" for h in hits[:4])
    out = llm.text(SONNET, _PROSE_SYSTEM, f"Question: {question}\n\nSections:\n{sections}", max_tokens=1024)
    text = ""
    if out and out.strip() != "NONE":
        text = _clean_prose(out)
    if not text:
        text = _extractive(question, top.text)
        if not text:
            return refusal(question)
    return Answer(text=text, sources=[Source(top.url, snapshot_id, top.text[:200])])


def refusal(question: str) -> Answer:
    return Answer(text=_REFUSAL, confident=False)


def sanitize(answer: Answer, keep: set[str], drop: set[str]) -> Answer:
    keep_n = set().union(*(_numbers(k) for k in keep)) if keep else set()
    drop_n = set().union(*(_numbers(d) for d in drop)) if drop else set()
    out = []
    for line in answer.text.split("\n"):
        kept = []
        for s in _sentences(line):
            n = _numbers(s)
            if _BANNED.search(s) or (n & drop_n and not n & keep_n):
                continue
            kept.append(s)
        if kept:
            out.append(" ".join(kept))
    text = "\n".join(out).strip()
    if not text:
        return Answer(text=_REFUSAL, confident=False)
    answer.text = text
    return answer
