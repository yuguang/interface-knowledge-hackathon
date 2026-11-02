"""Answer composition: fact templates, prose composer, refusal, and the stale-value sanitizer."""
from __future__ import annotations

import re
from typing import Optional

from contract import Answer, Source
from scoring import _numbers

from . import tracing
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
_ABBR_END = re.compile(r"\b(?:St|Ave|Blvd|Rd|Dr|Ste|Mt|Ft|Mr|Mrs|Ms|Jr|Sr|Hwy)\.$", re.I)
# Whole-word weekday, full name or standard abbreviation, followed by a non-letter (or end): "Saturday", "Mon-Fri",
# "Thurs,". Words that merely start with a day stem ("Monitor", "Saturated", "Wedge") do not match.
_DAY_START = re.compile(r"(?:monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu"
                        r"|friday|fri|saturday|sat|sunday|sun)(?![a-z])", re.I)


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


_DAY_WORDS = {"mon-fri": "Monday-Friday", "sat": "Saturday", "sun": "Sunday"}
_SLUG_WORDS = {"hy-vee": "Hy-Vee"}
_NAME_FIRST = {"credit_card", "checking", "money_market", "ira", "share_savings", "certificate", "certificate_special"}


def _deslug(slug: str) -> str:
    """"platinum-visa" -> "Platinum Visa", "hy-vee" -> "Hy-Vee"."""
    if slug in _SLUG_WORDS:
        return _SLUG_WORDS[slug]
    return " ".join(w if w[:1].isdigit() else w.capitalize() for w in slug.split("-") if w)


def _amount(n: str) -> str:
    try:
        v = float(n)
    except ValueError:
        return n
    return f"${v:,.2f}" if v != int(v) else f"${int(v):,}"


def _band_text(band: str) -> str:
    """"25000-inf" -> "$25,000 and up", "10000-24999.99" -> "$10,000-$24,999.99", "0-100000" -> "up to $100,000"."""
    lo, sep, hi = band.partition("-")
    if not sep:
        return band
    if hi == "inf":
        return f"{_amount(lo)} and up"
    if lo in ("0", "0.0"):
        return f"up to {_amount(hi)}"
    return f"{_amount(lo)}-{_amount(hi)}"


def _model_year_text(band: str) -> str:
    lo, sep, hi = band.partition("-")
    if not sep or lo == hi:
        return lo
    if lo == "0":
        return f"{hi} and older"
    if hi == "9999":
        return f"{lo} and newer"
    return band


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
    if "name" in q and product in _NAME_FIRST:
        pre.append(_deslug(q["name"]))
    label = _PRODUCT_LABEL.get(product, product.replace("_", " "))
    if "model_year" in q:
        post.append(f"{_model_year_text(q['model_year'])} model-year vehicles")
    if "tier" in q:
        post.append(f"balances of {_band_text(q['tier'])}")
    if "loan_amount" in q:
        lt = _band_text(q["loan_amount"])
        post.append(f"loan amounts {lt}" if lt.startswith("up to") else f"loan amounts of {lt}")
    if "name" in q and product not in _NAME_FIRST:
        post.append(_deslug(q["name"]))
    if "what" in q:
        post.append(q["what"])
    if "day" in q:
        post.append(_DAY_WORDS.get(q["day"], q["day"]))
    return " ".join(pre), label, " and ".join(post), q


def _sentence(s: str) -> str:
    """End with exactly one period ("5:30 p.m." is not followed by another)."""
    return s if s.endswith(".") else s + "."


def _is_range(v: str) -> bool:
    return bool(re.search(r"\d\s*%?\s*-\s*\d", v))


def _line(fact: ResolvedFact, rate: Optional[ResolvedFact] = None) -> str:
    """One sentence for one fact, using the site's wording (e.g. "as low as 5.39% APR"). `rate` is the same
    account's plain rate, shown after its APY/APR for a generic "rate" question."""
    product, attribute, _ = parse_key(fact.fact_key)
    pre, label, post, q = _subject_parts(fact.fact_key)
    v = fact.value
    if attribute in ("apr", "apy"):
        unit = attribute.upper()
        verb = "" if _is_range(v) else ("as low as " if attribute == "apr" else "up to ")
        tail = ""
        if rate is not None:
            tail = f", with {'a dividend' if attribute == 'apy' else 'an interest'} rate of {rate.value}"
        if product == "auto_loan":
            prefix = f"The {pre + ' ' if pre else ''}{unit}"
            if "model_year" in q:
                return f"{prefix} for {_model_year_text(q['model_year'])} model-year vehicles is {verb}{v} {unit}{tail}."
            return f"{prefix} for an auto loan is {verb}{v} {unit}{tail}."
        who = f"{pre + ' ' if pre else ''}{label}"
        extra = f" for {post}" if post else ""
        return f"The {who} {unit}{extra} is {verb}{v} {unit}{tail}."
    what = {"rate": "rate", "min_balance": "minimum balance", "hours_lobby": "lobby hours",
            "hours_driveup": "drive-up hours", "address": "address", "phone": "phone number", "status": "status",
            "closure_date": "closure date", "routing_number": "routing number", "count": "count", "url": "web address",
            "award": "award", "eligibility": "eligibility", "detail": "detail"}.get(attribute, attribute.replace("_", " "))
    verb = "are" if attribute in ("hours_lobby", "hours_driveup") else "is"
    who = f"{pre + ' ' if pre else ''}{label}"
    if product == "branch" and "name" in q:
        who = f"{_deslug(q['name'])} branch"
        if attribute in ("hours_lobby", "hours_driveup") and "day" in q:
            return _sentence(f"The {who} {what} {verb} {v} {_DAY_WORDS.get(q['day'], q['day'])}")
        post = ""
    extra = f" for {post}" if post else ""
    return _sentence(f"The {who} {what}{extra} {verb} {v}")


def _lines(selected: list[ResolvedFact]) -> list[str]:
    """One line per fact; an APY/APR fact and the plain rate of the same account share one line."""
    out, used = [], set()
    for i, r in enumerate(selected):
        if i in used:
            continue
        p, a, q = parse_key(r.fact_key)
        partner = None
        if a in ("apy", "apr"):
            for j in range(i + 1, len(selected)):
                pj, aj, qj = parse_key(selected[j].fact_key)
                if j not in used and aj == "rate" and pj == p and qj == q:
                    partner = selected[j]
                    used.add(j)
                    break
        out.append(_line(r, partner))
    return out


@tracing.traced("kb.render_facts", "tool")
def render_facts(question: str, intent: Intent, selected: list[ResolvedFact], snapshot_id: str) -> Answer:
    text = "\n".join(_lines(selected))
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
    "Never ask the member a question. Answer in at most 3 sentences. Each section shows its page URL and the "
    "targets of links it contains; when the question asks for a link, web address or where to go online, give the "
    "URL of the page or link that serves it (a section's own page URL counts). "
    "Answer directly; never describe what the sections lack or answer a different question. "
    "If the sections do not answer the question, reply with exactly: NONE"
)
# The model saying the sections lack the asked value, or ending with NONE after an explanation: not an answer.
_DISCLAIMER = re.compile(r"\b(?:sections?|provided (?:information|text)|information provided)\b[^.]*?"
                         r"\b(?:don't|do not|doesn't|does not|didn't)\s+(?:state|give|include|list|mention|say"
                         r"|specify|provide|contain|show)", re.I)
_NONE_TOKEN = re.compile(r"(?:^|[.!?]\s+|\n\s*)NONE\.?\s*$")  # NONE as its own trailing sentence, not "Fee: NONE."
MAX_SECTION_LINKS = 8


def _section(h: Hit, links: Optional[dict], question: str = "") -> str:
    """One section for the prose prompt: page URL and heading, text, then targets of links whose text it shows as
    whole words. Nav links come first in page order, so candidates rank by anchor overlap with the question
    (page order breaks ties) before the cap."""
    flat = " ".join(h.text.split())
    qw = _words(question)
    cands = [(i, t, href) for i, (t, href) in enumerate((links or {}).get(h.url, []))
             if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", flat)]
    cands.sort(key=lambda c: (-len(qw & _words(c[1])), c[0]))
    shown = [f"{t}: {href}" for _, t, href in cands[:MAX_SECTION_LINKS]]
    head = f"[{h.url}" + (f" | {h.heading_path}]" if h.heading_path else "]")
    return head + "\n" + h.text + ("\nLinks: " + "; ".join(shown) if shown else "")


def _clean_prose(text: str) -> str:
    """Drop questions and banned-phrase sentences from model output."""
    sents = [s for s in _sentences(text.strip()) if s and not s.rstrip().endswith("?") and not _BANNED.search(s)]
    return " ".join(sents).strip()


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP}


def _strip_prefix(text: str, heading_path: str = "") -> str:
    """Drop the chunker's first line "Title | Dupaco > Heading > ..." (normalize.chunk_page) from a chunk text."""
    first, nl, rest = text.partition("\n")
    if nl and rest.strip():
        hp = (heading_path or "").strip()
        if (hp and (first.strip() == hp or first.rstrip().endswith(" > " + hp))) \
                or (" | " in first and not first.lstrip().startswith("|")):
            return rest
    return text


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


_ACCOUNT_NOUNS = {"visa", "ira"}


def _name_in_sections(intent: Intent, hits: list[Hit]) -> bool:
    """A product question naming a specific account (intent qualifier `name`, e.g. "platinum-elite-visa") is only
    answerable from sections that mention every word of that name; branch/org/program names are not checked."""
    name = (intent.qualifiers or {}).get("name")
    if not name or not intent.product or intent.product in ("branch", "org", "program"):
        return True
    want = {t for t in name.split("-") if t and t not in _ACCOUNT_NOUNS}
    have = set(re.findall(r"[a-z0-9]+", " ".join(h.text for h in hits).lower()))
    return want <= have


@tracing.traced("kb.render_prose", "chain")
def render_prose(question: str, intent: Intent, hits: list[Hit], snapshot_id: str, llm: LLM,
                 links: Optional[dict[str, list[tuple[str, str]]]] = None) -> Answer:
    """`links`: page url -> (link text, target) pairs of that page (Store.links)."""
    top = hits[0]
    if not _name_in_sections(intent, hits[:4]):
        return refusal(question)  # "Sapphire Money Market": the named account appears in none of the sections
    sections = "\n\n".join(_section(h, links, question) for h in hits[:4])
    out = llm.text(SONNET, _PROSE_SYSTEM, f"Question: {question}\n\nSections:\n{sections}", max_tokens=1024)
    if out is not None and (out.strip().rstrip(".").upper() == "NONE" or _NONE_TOKEN.search(out)
                            or _DISCLAIMER.search(_sentences(out.strip())[0] if out.strip() else "")):
        return refusal(question)  # the sections don't answer it
    text = _clean_prose(out) if out else ""
    if not text:  # LLM failure (None) or nothing usable left: extract from the top section
        text = _extractive(question, _strip_prefix(top.text, top.heading_path))
        if not text:
            return refusal(question)
    return Answer(text=text, sources=[Source(top.url, snapshot_id, top.text[:200])])


@tracing.traced("kb.refusal", "tool", tags=["guardrail"])
def refusal(question: str) -> Answer:
    return Answer(text=_REFUSAL, confident=False)


@tracing.traced("kb.sanitize", "tool", tags=["guardrail"])
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
