"""Rule-based intent parser with a closed-label LLM fallback.

`parse` never raises on a string input: rules decide family/product/attribute/qualifiers; only when no
product is found (and an LLM is supplied) does a cached, closed-label Haiku classification get a say.

A generic rate word ("rate", "interest rate", "rates") yields a fact intent with a product and attribute None:
the site quotes deposits as APY and loans/cards as APR, so resolve.select_for_intent then covers rate plus
APY/APR (resolve.rate_attributes). Explicit "APY", "APR" and "dividend rate" keep their exact attribute.
"""
from __future__ import annotations

import re
from typing import Optional

from . import keys, tracing
from .llm import HAIKU, LLM
from .model import Intent

_DEPOSITS = {"certificate", "certificate_special", "share_savings", "money_market", "checking",
             "bright_savings", "jump_start", "ira"}
_NO_SEGMENT = {"branch", "org", "program"}

# Product patterns, most specific first (matched against the lower-cased question).
_PRODUCTS = [
    ("credit_card", r"credit cards?|\bvisa\b|\bmastercard\b"),
    ("construction_loan", r"construction (?:loan|mortgage)"),
    ("rv_loan", r"\brv\b|recreational vehicle"),
    ("auto_loan", r"auto(?:mobile)? loans?|car loans?|vehicle loans?|auto financing"),
    ("student_loan", r"student loans?"),
    ("personal_loan", r"personal loans?"),
    ("heloc", r"\bheloc\b|home equity line"),
    ("home_equity_loan", r"home equity loans?"),
    ("mortgage_arm", r"\barm\b|adjustable[- ]rate|\d\s*/\s*\d\s*arm"),
    ("mortgage_fixed", r"mortgages?|home loans?"),
    ("bright_savings", r"bright savings"),
    ("jump_start", r"jump ?start"),
    ("money_market", r"money market"),
    ("checking", r"checking"),
    ("ira", r"\biras?\b|individual retirement"),
    ("certificate", r"certificates?|\bcds?\b"),
    ("share_savings", r"share savings|savings"),
]
_CERT = r"certificates?|\bcds?\b"

_STOP = set("""a an the of to for and or in on at is are was were be do does did can could will would you your
i me my we our us it its this that these those with about any some what which who whom how when where why
tell give show get please there their them have has had as by from than then so if not no yes right now
current currently""".split())
_QWORD = re.compile(r"^(what|which|how|when|where|who|why|can|could|do|does|is|are|will|tell|show|give|list|"
                    r"provide|get|i would like|i'd like|i want)\b", re.I)
_ATTR_WORDS = re.compile(r"\b(rates?|aprs?|apys?|yield|dividends?|hours|address|phone|routing|branch(?:es)?|link|url|"
                         r"balance|minimum|eligible|eligibility|recognition|award|close[sd]?)\b", re.I)
_GREETING = re.compile(r"^\s*(?:(?:hi|hello|hey|howdy|good (?:morning|afternoon|evening)|thanks|thank you)\b"
                       r"(?:\s+(?:there|dupaco))?\s*[,!.:-]*\s*)+", re.I)

_GENERIC_RATE = "rate*"  # internal marker; never leaves parse()

_NAME_STOP = {"the", "what", "is", "are", "dupaco", "dupaco's", "a", "an", "of", "for", "your", "our", "which",
              "when", "where", "how", "does", "do", "can", "and", "or", "any", "hours", "address", "at", "in", "to",
              "my", "each", "every", "all", "this", "that", "other", "nearest", "closest", "local", "main",
              "lobby", "drive-up", "from", "about", "does", "still", "same", "your", "their", "its", "on"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _amount(num: str) -> str:
    v = num.replace(",", "")
    if "." in v:
        w, f = v.split(".", 1)
        f = f.rstrip("0")
        return f"{int(w)}.{f}" if f else str(int(w))
    return str(int(v))


def _find_product(low: str) -> Optional[str]:
    # "special" turns a certificate into a certificate special
    if re.search(_CERT, low) and re.search(r"\bspecials?\b", low):
        return "certificate_special"
    for name, pat in _PRODUCTS:
        if re.search(pat, low):
            return name
    return None


def _branch_name(q: str) -> Optional[str]:
    """Words directly before "branch"/"location" (walking back to the first stopword), as a hyphen slug."""
    m = re.search(r"((?:[\w'.-]+\s+){0,3})(?:branch|location|office)\b", q, re.I)
    if not m:
        return None
    words = []
    for w in reversed(m.group(1).split()):
        if w.lower() in _NAME_STOP:
            break
        words.append(w)
    return _slug(" ".join(reversed(words))) or None


# Account nouns whose preceding words can name a specific account ("Achieve Money Market", "Roth IRA").
_NAMED_NOUN = {
    "credit_card": r"\b(?:visa|mastercard)\b|\bcredit cards?\b",
    "money_market": r"\bmoney market\b",
    "checking": r"\bchecking\b",
    "share_savings": r"\bsavings\b",
    "ira": r"\biras?\b",
}
# Words before the noun that describe rather than name the account.
_NAME_GENERIC = {"business", "personal", "regular", "fixed", "jumbo", "term", "share", "special", "standard",
                 "new", "1st", "first", "rate", "rates", "interest", "dividend", "apy", "apr", "account", "accounts",
                 "card", "cards", "credit", "basic", "plain", "current", "member", "members", "highest", "lowest",
                 "best", "top", "maximum", "max", "minimum", "min", "greatest", "cheapest", "smallest", "typical",
                 "average", "available", "offered", "open", "online", "dupaco"}


def _product_name(low: str, product: str) -> Optional[str]:
    """Slug of the words naming a specific account just before its noun ("sapphire" in "Sapphire Money Market",
    "platinum-elite-visa" in "Platinum Elite Visa"); None for a generic mention ("business checking")."""
    pat = _NAMED_NOUN.get(product)
    m = re.search(pat, low) if pat else None
    if not m:
        return None
    words = []
    for w in reversed(re.findall(r"[a-z0-9$'.-]+", low[:m.start()])[-4:]):
        w = w.strip(".'")
        if w in _NAME_STOP or w in _STOP or w.endswith("'s") or w.startswith("$"):
            break
        if w in _NAME_GENERIC or re.search(r"\d", w):
            continue
        words.append(w)
    if not words:
        return None
    tokens = list(reversed(words))
    if product == "credit_card":  # card names are keyed "<name>-visa" (tables); a Mastercard keeps its own noun
        tokens.append("mastercard" if "mastercard" in m.group(0) else "visa")
    return _slug(" ".join(tokens))


def _attribute(low: str) -> tuple[Optional[str], Optional[str]]:
    """(attribute, forced product) from the wording."""
    if re.search(r"routing|\brtn\b|\baba\b", low):
        return "routing_number", "org"
    if re.search(r"how many (?:dupaco )?(?:branch|location|office)|number of (?:branch|location|office)", low):
        return "count", "org"
    if re.search(r"\bhours\b|\bopen (?:until|late|on)\b", low):
        return ("hours_driveup" if re.search(r"drive[- ]?(?:up|thru|through)", low) else "hours_lobby"), "branch"
    if re.search(r"\bclos(?:e|es|ing|ure)\b", low) and re.search(r"branch|hy-?vee|location", low):
        return "closure_date", "branch"
    if re.search(r"still open|open today|\bis .* open\b|already closed|\bis .* closed", low) \
            and re.search(r"branch|hy-?vee|location", low):
        return "status", "branch"
    if re.search(r"\b(?:update|change|correct)\b.*\b(?:address|phone|contact)\b|\bon file\b", low):
        return None, None  # the member's own contact details, not a branch address/phone (para-dup-060)
    if re.search(r"\baddress\b|\bwhere is\b.*\bbranch", low):
        return "address", "branch"
    if re.search(r"\bphone\b|call you|telephone", low):
        return "phone", None
    if re.search(r"\b(?:link|url)\b|\bwhere\b[^?]*\b(?:website|web ?site|online)\b", low):
        return "url", None
    if re.search(r"recogni(?:tion|[sz]ed?)|\bawards?\b|\bwon\b", low):
        return "award", "org"
    if re.search(r"eligible|eligibility|who can join|qualify to (?:join|become)", low):
        return "eligibility", "org"
    if re.search(r"minimum (?:balance|deposit|to open)|min\.? balance", low):
        return "min_balance", None
    if re.search(r"\baprs?\b|annual percentage", low):
        return "apr", None
    if re.search(r"\bapys?\b|\byields?\b|annual percentage yield", low):
        return "apy", None
    if re.search(r"\bdividend rates?\b", low):
        return "rate", None
    if re.search(r"\brates?\b|\bdividends?\b|\binterest\b", low):
        return _GENERIC_RATE, None
    return None, None


def _clean(question: str) -> str:
    """Drop greetings and sentences/clauses with no product, attribute or question signal."""
    q = _GREETING.sub("", question.strip())
    parts = re.split(r"(?<=[.!?:;])\s+", q)
    kept = []
    for p in parts:
        if (_find_product(p.lower()) or _ATTR_WORDS.search(p) or _QWORD.match(p.strip())
                or p.strip().endswith("?")):
            kept.append(p.strip().rstrip(":;").strip())
    out = " ".join(k for k in kept if k)
    return out or question.strip()


def _qualifiers(q: str, low: str, product: str, name: Optional[str], attribute: Optional[str] = None) -> dict[str, str]:
    quals: dict[str, str] = {}
    if product == "org" and attribute == "count":  # C2 keys branch counts as org|count|what=branches
        return {"what": "branches"}
    if product in _NO_SEGMENT:
        if name:
            quals["name"] = name
        return quals
    quals["segment"] = "business" if re.search(r"\bbusiness\b", low) else "personal"
    m = re.search(r"(?<!\d)(\d{1,3})\s*-?\s*(months?|mos?\b|years?|yrs?\b)", low)
    if m:
        n = int(m.group(1))
        quals["term"] = str(n * 12 if m.group(2).startswith("y") else n)
    if product in ("auto_loan", "rv_loan"):
        y = (re.search(r"model[- ]year\s*(?:of\s*)?((?:19|20)\d\d)\b", low)
             or re.search(r"\b((?:19|20)\d\d)\s*(?:model[- ]year|model|vehicle|car|truck|suv|auto)\b", low))
        if y:
            quals["model_year"] = y.group(1)
    if re.search(r"\bjumbo\b", low):
        quals["kind"] = "jumbo"
    if product != "credit_card":
        a = re.search(r"\$\s*(\d[\d,]*(?:\.\d+)?)", q)
        if a:
            quals["tier" if product in _DEPOSITS else "loan_amount"] = _amount(a.group(1))
    if name:
        quals["name"] = name
    else:
        pn = _product_name(low, product)
        c = re.search(r"\b(platinum|rewards)\b", low) if product == "credit_card" else None
        if pn:
            quals["name"] = pn
        elif c:  # "Visa Platinum card": the card name after the noun
            quals["name"] = f"{c.group(1)}-visa"
    return quals


def _content_words(low: str) -> list[str]:
    return sorted({w for w in re.findall(r"[a-z0-9$%.]+", low) if w not in _STOP and len(w) > 1})


def _safe(quals: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in quals.items():
        if k in keys.QUALIFIERS:
            out[k] = re.sub(r"[;=|]", "", str(v))
    return out


def _canonical(family: str, product, attribute, quals, superlative, low: str) -> str:
    if family == "prose" or not product or not attribute:
        return "prose|" + " ".join(_content_words(low))
    if attribute == _GENERIC_RATE:
        return keys.make_key(product, "rate", quals) + "|" + family + "|" + (superlative or "") + "|generic"
    return keys.make_key(product, attribute, quals) + "|" + family + "|" + (superlative or "")


_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "enum": list(keys.PRODUCTS) + ["none"]},
        "attribute": {"type": "string", "enum": list(keys.ATTRIBUTES) + ["none"]},
    },
    "required": ["product", "attribute"],
}
_SYSTEM = ("Classify a question about the Dupaco credit union website into one product label and one attribute "
           "label from the allowed enums. Use 'none' when the question is not about a specific product fact "
           "(general, how-to, or descriptive questions).")


def _llm_labels(question: str, llm: LLM) -> tuple[Optional[str], Optional[str]]:
    try:
        out = llm.json(HAIKU, _SYSTEM, question, _SCHEMA, max_tokens=200)
    except Exception:
        return None, None
    if not isinstance(out, dict):
        return None, None
    p, a = out.get("product"), out.get("attribute")
    if p in keys.PRODUCTS and a in keys.ATTRIBUTES:
        return p, a
    return None, None


@tracing.traced("kb.intent", "chain", tags=["routing"])
def parse(question: str, llm: Optional[LLM] = None) -> Intent:
    question = question if isinstance(question, str) else str(question)
    cleaned = _clean(question)
    q = cleaned
    low = q.lower()

    attribute, forced = _attribute(low)
    product = _find_product(low)
    name = None
    if attribute in ("hours_lobby", "hours_driveup", "address", "closure_date", "status", "count") \
            or product is None and re.search(r"\bbranch", low):
        name = _branch_name(q)
    if forced:
        # a branch/org-only attribute overrides rate-ish product words ("Hy-Vee branch", "routing number")
        product = forced if forced == "org" or product in (None, "checking", "share_savings") else forced
    if product is None and re.search(r"\bbranch(?:es)?\b", low) and attribute in ("url", "phone"):
        product = "branch"
    if product is None and attribute == "phone":
        product = "org"
    if attribute == "award":  # the awarding organisation: "from Forbes", else a capitalised non-Dupaco word
        m = re.search(r"\bfrom ([A-Z][\w-]*)", q) or re.search(r"(?<=\s)(?!Dupaco)([A-Z][\w-]*)", q)
        name = _slug(m.group(1)) if m else None
    if product is None and re.search(r"home\$tart|homestart", low):
        product, attribute, name = "program", attribute or "detail", "homestart"

    if product is None and llm is not None:
        p, a = _llm_labels(cleaned, llm)
        if p:
            product, attribute = p, a

    superlative = None
    if re.search(r"\b(highest|best|top|maximum|greatest)\b", low):
        superlative = "highest"
    elif re.search(r"\b(lowest|cheapest|smallest)\b", low):
        superlative = "lowest"

    if product is None or attribute is None or (attribute == _GENERIC_RATE and product in _NO_SEGMENT):
        return Intent("prose", None, None, {}, None, cleaned,
                      _canonical("prose", None, None, {}, None, low))

    quals = _safe(_qualifiers(q, low, product, name, attribute))
    plural = re.search(r"credit cards|certificates\b|\bcds\b|branches|\btiers\b", low) and attribute != "count"
    family = "list" if (re.search(r"\b(all|list|every)\b", low) or re.search(r"\bany\b.*\btiers?\b", low)
                        or re.search(r"\b(which|what)\b.*\b(offer|have)\b", low) or plural) else "fact"
    canonical = _canonical(family, product, attribute, quals, superlative, low)
    return Intent(family, product, None if attribute == _GENERIC_RATE else attribute, quals, superlative, cleaned,
                  canonical)
