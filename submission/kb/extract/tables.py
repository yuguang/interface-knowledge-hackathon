"""Markdown rate-table extractor: header -> qualifier/value mapping, one FactObs per value cell."""
from __future__ import annotations

import re
from typing import Optional

from .. import keys
from ..model import FactObs, PageContext

_VALUE_ROLES = {"apr": "apr", "apy": "apy", "rate": "rate"}
_REF_RE = re.compile(r"or less|and under|or under")


def _header_role(h: str) -> str:
    """Role of a column: apr|apy|rate (values), term|tier|minimum|loan_amount|model_year (qualifiers),
    ignore (fees, points...), or label (describes the row)."""
    s = re.sub(r"\(.*?\)", " ", h.lower())
    s = re.sub(r"(?<=[a-z\)])\d+$", "", s.strip()).strip()
    s = re.sub(r"[^a-z.\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if "annual percentage rate" in s or s == "apr":
        return "apr"
    if "annual percentage yield" in s or s == "apy":
        return "apy"
    if s in ("rate", "dividend rate", "interest rate"):
        return "rate"
    if s == "term":
        return "term"
    if s in ("balance tier", "balance", "tier"):
        return "tier"
    if s in ("minimum balance", "min. to earn", "min to earn", "minimum deposit"):
        return "minimum"
    if s == "loan amount":
        return "loan_amount"
    if s in ("loan program", "model year"):
        return "model_year"
    if re.match(r"(min\.?|minimum|points?|payment|\*payment|fee|fees)\b", s):
        return "ignore"
    return "label"


_GENERIC = re.compile(r"(?i)^(variable |standard |fixed |introductory |current )?(interest )?(rate|apr|apy)?"
                      r"\s*\d*$|^(purchase|standard|card type|account type|rates?|apr)\W*\d*$")


def _slug(text: str) -> str:
    """Lowercase hyphen slug for `name` qualifiers; segment words and footnote digits are dropped."""
    t = re.sub(r"\s*\d+$", "", text.strip().lower())
    t = re.sub(r"\b(business|personal)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _is_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", line.strip()))


def _tables(md: str):
    """Yield (context_text, header_cells, row_lines) for every markdown table."""
    lines = md.splitlines()
    heading = ""
    recent: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        st = ln.strip()
        if st.startswith("|") and i + 1 < len(lines) and _is_sep(lines[i + 1]):
            header = _cells(ln)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(lines[j].strip())
                j += 1
            yield " ; ".join([heading] + recent[-2:]), header, rows
            recent = []
            i = j
            continue
        if st.startswith("#"):
            heading = st.lstrip("#").strip()
            recent = []
        elif st and len(st) <= 120:
            recent.append(st)
        elif st:
            recent = []
        i += 1


_PRODUCT_RULES = (
    (r"\b(ira|roth|traditional)\b", "ira"),
    (r"money market", "money_market"),
    (r"bright savings", "bright_savings"),
    (r"share savings", "share_savings"),
    (r"checking", "checking"),
    (r"credit card|\bvisa\b|card type|balance transfer", "credit_card"),
    (r"heloc|line of credit", "heloc"),
    (r"\bcertificates?\b.*\bspecial|\bspecial\b", "certificate_special"),
    (r"\bcertificates?\b|\bshare certificate", "certificate"),
    (r"\barm\b|adjustable", "mortgage_arm"),
    (r"fixed.*(conventional|mortgage|home|purchase|refinance)|(conventional|mortgage|home loan|purchase|refinance).*fixed",
     "mortgage_fixed"),
    (r"construction", "construction_loan"),
    (r"student", "student_loan"),
    (r"\brv\b|recreational|\bboat|camper|\batv\b", "rv_loan"),
    (r"home equity", "home_equity_loan"),
    (r"personal loan", "personal_loan"),
    (r"\bauto\b|vehicle|\bcar\b", "auto_loan"),
)


# Named accounts with no product in the fact-key vocabulary: skipping beats polluting a neighbour's keys.
_UNMAPPED = re.compile(r"launchpad|you name it|holiday club|high-yield|health savings")


def _product(*sources: str) -> Optional[str]:
    if _UNMAPPED.search(sources[0].lower()):
        return None
    for src in sources:
        s = src.lower()
        for pat, prod in _PRODUCT_RULES:
            if re.search(pat, s):
                return prod
    return None


def _band(s: str, minimum: bool = False) -> Optional[str]:
    if _REF_RE.search(s.lower()):
        return keys.norm_band("up to " + _REF_RE.sub("", s.lower()))
    return keys.norm_band(s, minimum=minimum)


_RANGE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*%?\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*%\s*\d?\s*(?:APR|APY)?\s*\d?\s*", re.I)


def _percent_range(cell: str) -> Optional[str]:
    """A two-ended rate range as "lo% - hi%" (each end via norm_percent); anything else is not one value."""
    m = _RANGE.fullmatch(cell)
    if not m:
        return None
    lo, hi = keys.norm_percent(m.group(1) + "%"), keys.norm_percent(m.group(2) + "%")
    if lo is None or hi is None or float(lo[:-1]) > float(hi[:-1]):
        return None
    return f"{lo} - {hi}"


def _row_obs(ctx: PageContext, ctx_text: str, header: list[str], line: str, chunk_id: Optional[int]):
    roles = [_header_role(h) for h in header]
    cells = _cells(line)
    if len(cells) != len(header):
        return []
    by_role: dict[str, list[tuple[int, str]]] = {}
    for idx, (r, c) in enumerate(zip(roles, cells)):
        by_role.setdefault(r, []).append((idx, c))
    values = [(r, c) for r in _VALUE_ROLES for _, c in by_role.get(r, [])]
    if not values:
        return []
    # a Term cell that is really a program description ("Refinance: 30-Year Fixed ... $100,000 or less")
    # is part of the label too
    label = " ".join([c for _, c in by_role.get("label", [])]
                     + [c for _, c in by_role.get("term", []) if re.search(r"[a-z]{4,}", c, re.I)
                        and re.search(r"fixed|conventional|refinance|purchase", c, re.I)]).strip()
    header_txt = " ".join(h for h, r in zip(header, roles) if r == "label")
    prod = _product(label, header_txt + " " + ctx_text, ctx.url + " " + ctx.title)
    if prod is None and not label and "term" in by_role and "apy" in by_role \
            and not _UNMAPPED.search(ctx_text.lower()):
        # "| Term | Dividend Rate | APY | Min. to Earn |" under a bare "Regular" tab (snapshots 01-03): a
        # term-by-APY deposit table with nothing else naming it is the regular certificate table.
        prod = "certificate"
    if prod is None:
        return []
    quals: dict[str, str] = {}
    quals["segment"] = "business" if "/business" in ctx.url.lower() else "personal"

    def one(role: str) -> Optional[str]:
        return next((c for _, c in by_role.get(role, []) if c), None)

    # explicit qualifier columns; a non-empty cell that fails to normalise invalidates the row
    t = one("term")
    if t:
        m = keys.norm_term_months(t)
        if m is None:
            return []
        quals["term"] = str(m)
    my = one("model_year")
    if my:
        v = keys.norm_model_year(my)
        if v is None:
            return []
        quals["model_year"] = v
    la = one("loan_amount")
    if la:
        v = _band(la)
        if v is None:
            return []
        quals["loan_amount"] = v
    ti = one("tier")
    if ti and re.search(r"\d", ti):
        v = _band(ti)
        if v is None:
            return []
        quals["tier"] = v
    # qualifiers carried in the label cell
    low = label.lower()
    if "term" not in quals and re.search(r"\d+\s*-?\s*(months?|years?|yrs?|mos?)\b", low):
        m = keys.norm_term_months(label)
        if m is not None and prod not in ("auto_loan", "rv_loan", "credit_card"):
            quals["term"] = str(m)
    if "$" in label and "tier" not in quals and "loan_amount" not in quals:
        if prod in ("mortgage_fixed", "mortgage_arm", "auto_loan", "rv_loan", "home_equity_loan", "heloc",
                    "personal_loan", "construction_loan", "student_loan"):
            v = _band(label)
            if v:
                quals["loan_amount"] = v
        else:
            v = _band(label)
            if v:
                quals["tier"] = v
    if "tier" not in quals and "loan_amount" not in quals:
        mn = one("minimum")
        if mn and "$" in mn and re.search(r"[1-9]", mn):
            v = keys.norm_band(mn, minimum=True)
            if v:
                quals["tier"] = v
    # kind / name
    jumbo_ctx = re.fullmatch(r"\W*\d+[\s-]*(months?|years?)\W*", low) is not None and "jumbo" in ctx_text.lower()
    if "jumbo" in low or jumbo_ctx:
        quals["kind"] = "jumbo"
    elif prod in ("mortgage_fixed", "mortgage_arm") and re.search(r"\brefinance\b", low):
        quals["kind"] = "refinance"
    if prod == "ira":
        quals["name"] = "roth" if "roth" in low else "traditional" if "traditional" in low else "ira"
    elif prod == "credit_card":
        # Checking gets no name: landing pages print the 1st Rate Checking table without a label, so a name from
        # /rates would split one account across two keys. Credit cards need it (platinum-visa vs rewards-visa).
        # nearest heading/caption line naming a card ("Rewards Visa" above a "Standard | 15.50% – 21.00%" table)
        heading = next((t for t in reversed(ctx_text.split(" ; ")) if re.search(r"(?i)visa|card", t)), "")
        first_hdr = next((h for h, r in zip(header, roles) if r == "label"), "")
        nm = next((_slug(c) for c in (label, first_hdr, heading) if c and not _GENERIC.match(c.strip())
                   and _slug(c)), "")
        nm = re.sub(r"^(.*?\bvisa)\b.*$", r"\1", nm)  # "Platinum Visa Rate" names the Platinum Visa
        if nm:
            quals["name"] = nm
    elif prod == "money_market" and "achieve" in low:
        quals["name"] = "achieve"

    out = []
    for attr, cell in values:
        if re.search(r"\d\s*%?\s*(-|–|—|to)\s*\d+(\.\d+)?\s*%", cell):
            norm = _percent_range(cell)  # "12.50% – 19.50%2" -> "12.50% - 19.50%"
        else:
            norm = keys.norm_percent(cell)
        if norm is None:
            continue
        try:
            key = keys.make_key(prod, attr, quals)
        except ValueError:
            continue
        out.append(FactObs(
            fact_key=key, product=prod, attribute=attr, qualifiers=dict(quals), value_raw=cell,
            value_norm=norm, snapshot_id=ctx.snapshot_id, taken_on=ctx.taken_on, url=ctx.url,
            page_type=ctx.info.page_type, authority=ctx.info.authority, page_date=ctx.info.page_date,
            quote=" | ".join(cells)[:200], extractor="table", chunk_id=chunk_id))
    return out


def _chunk_id(ctx: PageContext, line: str) -> Optional[int]:
    for sc in ctx.chunks:
        if line in sc.chunk.text:
            return sc.chunk_id
    return None


def extract(ctx: PageContext) -> list[FactObs]:
    out: list[FactObs] = []
    seen: set[tuple[str, str]] = set()
    for ctx_text, header, rows in _tables(ctx.markdown):
        for line in rows:
            try:
                obs = _row_obs(ctx, ctx_text, header, line, _chunk_id(ctx, line))
            except Exception:  # one bad row must not lose the page
                continue
            for o in obs:
                if (o.fact_key, o.value_norm) not in seen:
                    seen.add((o.fact_key, o.value_norm))
                    out.append(o)
    return out
