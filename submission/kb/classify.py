"""Page classifier: URL/title/table rules -> page type, authority, and page date."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from .model import AUTHORITY, PageInfo

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"])}
_MONTH_RE = "|".join(_MONTHS)
_NUM_DATE = r"(\d{1,2})/(\d{1,2})/(\d{4})"
_TXT_DATE = rf"({_MONTH_RE})\.? (\d{{1,2}}),? (\d{{4}})"

_EFFECTIVE = re.compile(rf"effective\s+(?:as\s+of\s+)?(?:{_NUM_DATE}|{_TXT_DATE})", re.I)
_AS_OF = re.compile(rf"as\s+of\s+(?:{_NUM_DATE}|{_TXT_DATE})", re.I)
_TEXT_DATE = re.compile(_TXT_DATE, re.I)
_URL_DATE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})(?:/|$)")
_RATE_HEADER = re.compile(r"apr|apy|dividend", re.I)
_BLOG_PATH = re.compile(r"/learn/blog/|/\d{4}/\d{2}/\d{2}/|/dupacodaily/\d{4}/")
_NEWS_PATH = re.compile(r"/news/|/newsroom|/press|/about/news|/resources/dupaco-news")
_DISCLOSURE_PATH = re.compile(r"\.pdf$|/disclosures|/privacy-policy")


def _has_rate_table(markdown: str) -> bool:
    return any(line.lstrip().startswith("|") and _RATE_HEADER.search(line) for line in markdown.splitlines())


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _from_match(m: re.Match) -> str | None:
    g = m.groups()
    if g[0] is not None:  # MM/DD/YYYY
        return _iso(int(g[2]), int(g[0]), int(g[1]))
    return _iso(int(g[5]), _MONTHS[g[3].lower()], int(g[4]))


def _page_date(path: str, markdown: str, page_type: str) -> str | None:
    for m in _EFFECTIVE.finditer(markdown):
        d = _from_match(m)
        if d:
            return d
    if page_type == "rate_sheet":
        for m in _AS_OF.finditer(markdown):
            d = _from_match(m)
            if d:
                return d
    m = _URL_DATE.search(path)
    if m:
        d = _iso(int(m[1]), int(m[2]), int(m[3]))
        if d:
            return d
    if page_type in ("blog", "news"):  # byline date near the top of the article
        m = _TEXT_DATE.search(markdown[:800])
        if m:
            return _iso(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
    return None


def _page_type(host: str, path: str, title: str, markdown: str) -> str:
    p = path.lower().rstrip("/")
    if host == "info.dupaco.com":
        return "legacy"
    if "/calculators/" in p + "/" or "-calculator" in p:
        return "calculator"
    if _NEWS_PATH.search(p + "/"):
        return "news"
    if _BLOG_PATH.search(p + "/"):
        return "blog"
    if _DISCLOSURE_PATH.search(p):
        return "disclosure"
    rate_table = _has_rate_table(markdown)
    if p.endswith("rates") or (rate_table and "rates" in title.lower()):
        return "rate_sheet"
    if ("/locations/" in p + "/" or "/branches/" in p + "/" or p.endswith("/contact")
            or "location" in title.lower()):
        return "branch"
    if "/promo" in p or "/special" in p or p.endswith("-lp") or ("special" in title.lower() and not rate_table):
        return "promo"
    if "/personal/" in p + "/" or "/business/" in p + "/":
        return "product"
    return "other"


def classify_page(url: str, title: str, markdown: str, taken_on: str) -> PageInfo:
    u = urlparse(url)
    page_type = _page_type(u.netloc.lower(), u.path, title or "", markdown or "")
    return PageInfo(page_type, AUTHORITY[page_type], _page_date(u.path, markdown or "", page_type))
