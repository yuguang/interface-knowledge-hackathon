"""Regex fact extractors: hours, address, phone, routing number, counts, closure dates, awards, program amounts.

Precision over recall: every pattern is anchored to a labelled line or a sentence naming the subject.
"""
from __future__ import annotations

import html
import re
from typing import Optional

from .. import keys
from ..model import FactObs, PageContext

# ---------------------------------------------------------------- text helpers

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"$(])")
_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
_DATE = rf"(?:{_MONTHS})\s+\d{{1,2}},\s+\d{{4}}"


def _clean(s: str) -> str:
    s = html.unescape(html.unescape(s))
    s = _LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("__", "")
    return re.sub(r"[ \t ]+", " ", s).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _lines(md: str) -> list[str]:
    return [_clean(ln) for ln in md.split("\n")]


def _sentences(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if not ln or ln.startswith("|") or ln.startswith("!"):
            continue
        out += [s.strip() for s in _SENT_SPLIT.split(ln) if s.strip()]
    return out


class _Emitter:
    def __init__(self, ctx: PageContext):
        self.ctx = ctx
        self.obs: list[FactObs] = []
        self._seen: set = set()
        self._chunks = [(sc.chunk_id, _clean(sc.chunk.text)) for sc in ctx.chunks]

    def chunk_id(self, quote: str) -> Optional[int]:
        probe = quote[:60]
        for cid, text in self._chunks:
            if probe and probe in text:
                return cid
        return None

    def add(self, product: str, attribute: str, quals: dict[str, str], raw: str, norm: str, quote: str) -> None:
        try:
            key = keys.make_key(product, attribute, quals)
        except ValueError:
            return
        quote = quote.strip()[:200]
        sig = (key, norm, quote)
        if sig in self._seen:
            return
        self._seen.add(sig)
        c = self.ctx
        self.obs.append(FactObs(key, product, attribute, dict(quals), raw, norm, c.snapshot_id, c.taken_on, c.url,
                                c.info.page_type, c.info.authority, c.info.page_date, quote, "regex",
                                self.chunk_id(quote)))


# ---------------------------------------------------------------- branch naming

_ADDR = re.compile(
    r"(\d{1,5}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][\w.'\- ]{2,40}?)(?:,|\s*\n|\s{2,}|\s)\s*"
    r"(?:(?:Suite|Ste\.?|Unit)\s*\w+,\s*)?([A-Z][A-Za-z.' ]{2,24}?),\s*(IA|IL|WI),?\s*(\d{5})(?:-\d{4})?\b")
_STREET_WORD = re.compile(r"\b(St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard|Hwy|Highway|Pkwy|Parkway|Ln|Lane|"
                          r"Way|Court|Ct|Circle|Cir|Pl|Place|Plaza|Trail|Pike|Loop|Square|Sq)\b\.?", re.I)
_LOCATED = re.compile(r"\blocated at \d{1,5}\s+((?:[NSEW]\.\s+)?[A-Z][\w.' ]{2,40}?)(?:[,;]| and | with | at |\.\s|$)")
_DATELINE = re.compile(r"^([A-Z][A-Z .]{2,30}),\s+(?:Iowa|Wis\.?|Wisconsin|Ill\.?|Illinois)\b")


def _addr_slug(street: str, city: str) -> str:
    """City + street slug of one address ("3299 Hillcrest Rd." in Dubuque -> "dubuque-hillcrest-rd"), so branches
    sharing a city get distinct keys (R43). The same slug keys a branch's address, phone and hours."""
    return slug(city + " " + re.sub(r"^\d+\s+", "", street.strip()))[:60]


def _branch_name(ctx: PageContext, lines: list[str], upto: int) -> str:
    """Branch slug (shared by hours, phone and address): nearest full address' city, else dateline city, else title slug."""
    order = list(range(min(upto, len(lines) - 1), -1, -1)) + list(range(upto + 1, len(lines)))
    for i in order:  # nearest preceding address, else the first following one
        m = _ADDR.search(lines[i])
        if m and _STREET_WORD.search(m.group(1)):
            return _addr_slug(m.group(1), m.group(2))
    city = next((slug(m.group(1)) for m in map(_DATELINE.match, lines[:40]) if m), None)
    if city:
        for ln in lines:
            m = _LOCATED.search(ln)
            if m:
                return f"{city}-{slug(m.group(1))}"[:60]
        return city
    return slug(ctx.title)[:40]


# ---------------------------------------------------------------- hours

_DAYS = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}
_DAYTOK = r"(?:Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:r|rs|rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)"
_TIME = r"(?:\d{1,2}(?::\d{2})?\s*[ap]\.?\s?m\.?|noon|midnight)"
_SEPR = r"\s*(?:-|–|—|to)\s*"
_HOURS_LINE = re.compile(
    rf"^[-*\s]*(?:(?P<kind>Lobby|Drive[- ]?(?:up|thru|through))(?:\s+hours)?\s*:?\s*)?"
    rf"(?P<d1>{_DAYTOK})(?:{_SEPR}(?P<d2>{_DAYTOK}))?\s*[:,]?\s*"
    rf"(?:(?P<t1>{_TIME}){_SEPR}(?P<t2>{_TIME})|(?P<closed>Closed))\b", re.I)
_KIND_LINE = re.compile(r"^[-*\s]*(Lobby|Drive[- ]?(?:up|thru|through))(?:\s+hours)?\s*:?\s*$", re.I)
_T = re.compile(r"(?:(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?|(noon|midnight))", re.I)


def _norm_time(t: str) -> Optional[str]:
    m = _T.fullmatch(t.strip())
    if not m:
        return None
    if m.group(4):
        return m.group(4).lower()
    h, mi = int(m.group(1)), m.group(2)
    if not 1 <= h <= 12:
        return None
    # keep the site's form: "8 a.m." stays whole-hour, "8:30 a.m." / "8:00 a.m." keep their minutes
    return f"{h}:{mi} {m.group(3).lower()}.m." if mi else f"{h} {m.group(3).lower()}.m."


def _day_quals(d1: str, d2: Optional[str]) -> list[str]:
    a = _DAYS[d1[:3].lower()]
    b = _DAYS[d2[:3].lower()] if d2 else None
    if b is None:
        return [a] if a in ("sat", "sun") else []
    if (a, b) == ("mon", "fri"):
        return ["mon-fri"]
    if (a, b) == ("sat", "sun"):
        return ["sat", "sun"]
    return []


def _hours(em: _Emitter, lines: list[str]) -> None:
    kind: Optional[str] = None
    for i, ln in enumerate(lines):
        if not ln:
            continue
        km = _KIND_LINE.match(ln)
        if km:
            kind = "hours_driveup" if "drive" in km.group(1).lower() else "hours_lobby"
            continue
        m = _HOURS_LINE.match(ln)
        if not m:
            kind = None
            continue
        if m.group("kind"):
            kind = "hours_driveup" if "drive" in m.group("kind").lower() else "hours_lobby"
        if m.group("closed"):
            norm = "Closed"
        else:
            t1, t2 = _norm_time(m.group("t1")), _norm_time(m.group("t2"))
            if not t1 or not t2:
                continue
            norm = f"{t1} - {t2}"
        raw = ln[m.start("d1"):m.end()].strip()
        name = _branch_name(em.ctx, lines, i)
        for day in _day_quals(m.group("d1"), m.group("d2")):
            em.add("branch", kind or "hours_lobby", {"day": day, "name": name}, raw, norm, ln)


# ---------------------------------------------------------------- address / phone

_PHONE = r"(?:\(\d{3}\)\s*|\b\d{3}[-.])\d{3}[-.]\d{4}\b"
_PHONE_LABEL = re.compile(rf"(?:\bphone(?: number)?\b)[^0-9\n]{{0,25}}?({_PHONE})", re.I)


def _norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p)
    return f"{d[:3]}-{d[3:6]}-{d[6:]}"


_ADDR_CUE = re.compile(r"\b(?:dupaco|branch|address|located)\b", re.I)


def _branch_address_context(lines: list[str], i: int) -> bool:
    """The address line, or the 3 non-empty lines before it, names a Dupaco branch/address and not an ATM
    (the contact page lists branches, surcharge-free ATMs; auto-loan pages list car dealers)."""
    before = [ln for ln in lines[max(0, i - 8):i] if ln.strip()][-3:]
    if any(re.search(r"\bATMs?\b", ln) for ln in before[-2:]):
        return False
    return any(_ADDR_CUE.search(ln) for ln in before + [lines[i]])


def _address(em: _Emitter, lines: list[str]) -> None:
    for i, ln in enumerate(lines):
        if not _branch_address_context(lines, i):
            continue
        # an address may be split over two lines: "123 Main St" / "City, IA 52001"
        text = ln if i == 0 else lines[i - 1] + " " + ln
        for cand in (ln, text):
            m = _ADDR.search(cand)
            if m and _STREET_WORD.search(m.group(1)):
                street, city, st, zip5 = m.group(1).strip(), m.group(2).strip(), m.group(3), m.group(4)
                norm = f"{street}, {city}, {st} {zip5}"
                em.add("branch", "address", {"name": _addr_slug(street, city)}, m.group(0).strip(), norm, cand)
                break


def _phone(em: _Emitter, lines: list[str]) -> None:
    for i, ln in enumerate(lines):
        m = _PHONE_LABEL.search(ln)
        if not m or "fax" in ln.lower()[:m.start(1)][-12:]:
            continue
        # Skip lines that list several numbers (ambiguous).
        if len(re.findall(_PHONE, ln)) != 1:
            continue
        near = ""
        for j in range(max(0, i - 3), i + 1):
            near += " " + lines[j]
        am = None
        for am in _ADDR.finditer(near):
            pass
        if am and _STREET_WORD.search(am.group(1)):
            em.add("branch", "phone", {"name": _addr_slug(am.group(1), am.group(2))}, m.group(1), _norm_phone(m.group(1)), ln)
        elif "located at" in ln:
            em.add("branch", "phone", {"name": _branch_name(em.ctx, lines, i)}, m.group(1), _norm_phone(m.group(1)), ln)
        elif re.match(r"^[-*\s]*phone\b", ln, re.I):
            em.add("org", "phone", {}, m.group(1), _norm_phone(m.group(1)), ln)


# ---------------------------------------------------------------- sentence level

_ROUTING = re.compile(r"(?<!\d)(\d{9})(?!\d)")
_OTHER_INST = re.compile(r"\b(?:Bank|Savings|Credit Union|Federal|Trust)\b")


def _aba_ok(d: str) -> bool:
    n = [int(c) for c in d]
    return (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + (n[2] + n[5] + n[8])) % 10 == 0


def _routing(em: _Emitter, lines: list[str], sentences: list[str]) -> None:
    contact = em.ctx.url.rstrip("/").lower().endswith("/contact")
    for s in sentences:
        if not re.search(r"routing|\bABA\b", s, re.I):
            continue
        found = [d for d in _ROUTING.findall(s) if _aba_ok(d)]
        if len(set(found)) != 1:
            continue
        stripped = re.sub(r"Dupaco(?:'s|’s)?(?: Community)?(?: Credit Union)?", "", s)
        names_dupaco = "dupaco" in s.lower()
        labelled = bool(re.match(r"^\W*routing number\s*:", s, re.I)) and contact
        ours = bool(re.search(r"\bour (?:routing|ABA)\b", s, re.I))
        if not (names_dupaco or labelled or ours):
            continue
        if _OTHER_INST.search(stripped):
            continue
        em.add("org", "routing_number", {}, found[0], found[0], s)


_COUNT = re.compile(r"\b(\d{2})\s+(?:full-service\s+)?(?:branches|branch (?:office )?locations|branch offices)\b", re.I)


def _counts(em: _Emitter, lines: list[str], sentences: list[str]) -> None:
    # News/blog boilerplate ("It has ... 22 branch office locations") is often the only place a count is stated.
    # It is kept (with the page's date) so the resolver's newest-date rule picks the current count over old releases.
    for s in sentences:
        m = _COUNT.search(s)
        if not m or re.search(r"leaders|Home Savings|acqui", s, re.I):
            continue
        # the sentence, or its line, must be about Dupaco
        if "dupaco" not in s.lower() and not any(s in ln and "dupaco" in ln.lower() for ln in lines):
            continue
        if int(m.group(1)) < 10:
            continue
        em.add("org", "count", {"what": "branches"}, m.group(1), m.group(1), s)


_CLOSE = re.compile(rf"\b(?:will (?:permanently |officially )?close|will be closed|is closing|closes|closing)\b"
                    rf"[^.]{{0,60}}?\b({_DATE})", re.I)
_CLOSE_NAME = re.compile(r"\b(?:The|the)\s+([A-Z][\w\-]*(?:\s+[A-Z][\w\-]*)?)\s+(?:branch|location|office)\b")
_CTX_NAME = re.compile(r"([A-Z][\w\-]+)\s+(?:store|branch)\b")


def _closures(em: _Emitter, lines: list[str], sentences: list[str]) -> None:
    for idx, s in enumerate(sentences):
        m = _CLOSE.search(s)
        if not m or not re.search(r"branch|location|office|Hy-Vee|store", s, re.I):
            continue
        name = None
        nm = _CLOSE_NAME.search(s)
        if nm and nm.group(1).lower() not in ("dupaco", "this"):
            name = slug(nm.group(1))
        if not name:
            tm = re.search(r"\bclos\w+\s+([A-Z][\w\-]+)\s+branch", em.ctx.title)
            if tm:
                name = slug(tm.group(1))
        if not name:
            prev = " ".join(sentences[max(0, idx - 2):idx])
            found = [x for x in _CTX_NAME.findall(prev) if x.lower() != "dupaco"]
            if found:
                name = slug(found[-1])
        if not name:
            continue
        date = m.group(1)
        em.add("branch", "closure_date", {"name": name}, date, date, s)


_ORD = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth")
_AWARD = re.compile(rf"\b({'|'.join(_ORD)})\s+time\b(?!\s+in just)", re.I)


def _byline_year(lines: list[str]) -> str:
    for ln in lines[:15]:
        m = re.fullmatch(rf"(?:{_MONTHS})\s+\d{{1,2}},\s+(\d{{4}})", ln)
        if m:
            return m.group(1)
    return ""


def _awards(em: _Emitter, lines: list[str], sentences: list[str]) -> None:
    """Keyed by year (R33): each year's ranking is its own fact, so dated releases never conflict with each other."""
    for s in sentences:
        if "forbes" not in s.lower() or "dupaco" not in s.lower():
            continue
        m = _AWARD.search(s)
        if not m:
            continue
        ym = re.search(r"\b(20\d\d)\b", s)
        year = ym.group(1) if ym else (em.ctx.info.page_date or _byline_year(lines))
        year = year[:4]
        if not re.fullmatch(r"20\d\d", year):
            continue
        em.add("org", "award", {"name": "forbes", "year": year}, m.group(1), m.group(1).lower(), s)


def _programs(em: _Emitter, lines: list[str]) -> None:
    for ln in lines:
        if not re.search(r"home\s?\$tart|homestart", ln, re.I):
            continue
        for s in [x.strip() for x in _SENT_SPLIT.split(ln)]:
            m = re.search(r"\bup to (\$\d[\d,]*)\b", s, re.I) or re.search(r"(\$\d[\d,]*)\s+in assistance", s, re.I)
            if m and re.search(r"assistance|grant|credit|down payment", s, re.I):
                v = keys.norm_money(m.group(1))
                if v:
                    em.add("program", "detail", {"name": "homestart"}, m.group(1), v, s)
                break


# ---------------------------------------------------------------- entry point


def extract(ctx: PageContext) -> list[FactObs]:
    em = _Emitter(ctx)
    lines = _lines(ctx.markdown)
    sentences = _sentences(lines)
    for fn in (lambda: _hours(em, lines), lambda: _address(em, lines), lambda: _phone(em, lines),
               lambda: _routing(em, lines, sentences), lambda: _counts(em, lines, sentences),
               lambda: _closures(em, lines, sentences), lambda: _awards(em, lines, sentences),
               lambda: _programs(em, lines)):
        try:
            fn()
        except Exception:  # one pattern failing must not drop the others
            continue
    return em.obs
