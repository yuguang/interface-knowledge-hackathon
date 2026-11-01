from collections import Counter

import pytest

from kb.classify import classify_page
from kb.model import AUTHORITY, PAGE_TYPES

PRODUCT = "https://www.dupaco.com/personal/loans/auto-loans"
RATE_TABLE = "| Account | Dividend Rate | APY |\n| --- | --- | --- |\n| Share | 0.20% | 0.20% |\n"


@pytest.mark.parametrize("url,t", [
    ("https://info.dupaco.com/auto", "legacy"),
    ("https://www.dupaco.com/calculators/auto-refinance", "calculator"),
    ("https://www.dupaco.com/business-calculators", "calculator"),
    ("https://www.dupaco.com/2018/09/25/understand-insurance-deductibles/", "blog"),
    ("https://www.dupaco.com/learn/blog/how-to-save", "blog"),
    ("https://www.dupaco.com/newsroom/dupaco-celebrates-75th-anniversary", "news"),
    ("https://www.dupaco.com/about/news/foo", "news"),
    ("https://www.dupaco.com/hubfs/fees.pdf", "disclosure"),
    ("https://www.dupaco.com/disclosures", "disclosure"),
    ("https://www.dupaco.com/personal/loans/auto-loans", "product"),
    ("https://www.dupaco.com/business/checking", "product"),
    ("https://www.dupaco.com/business/rates", "rate_sheet"),
    ("https://www.dupaco.com/rates?x=1", "rate_sheet"),
    ("https://www.dupaco.com/locations/dubuque", "branch"),
    ("https://www.dupaco.com/promo/spring", "promo"),
    ("https://www.dupaco.com/contact", "branch"),
    ("https://www.dupaco.com/about", "other"),
])
def test_url_types(url, t):
    info = classify_page(url, "", "", "2026-09-28")
    assert info.page_type == t
    assert info.authority == AUTHORITY[t]


def test_legacy_beats_other_rules():
    assert classify_page("https://info.dupaco.com/calculators/x", "", "", "2026-09-28").page_type == "legacy"


def test_rate_table_with_rates_title():
    url = "https://www.dupaco.com/personal/savings"
    assert classify_page(url, "Savings Rates | Dupaco", RATE_TABLE, "2026-09-28").page_type == "rate_sheet"
    assert classify_page(url, "Savings | Dupaco", RATE_TABLE, "2026-09-28").page_type == "product"


def test_special_title_without_rate_table_is_promo():
    url = "https://www.dupaco.com/personal/x"
    assert classify_page(url, "Holiday Special", "no table", "2026-09-28").page_type == "promo"
    assert classify_page(url, "Holiday Special Rates", RATE_TABLE, "2026-09-28").page_type == "rate_sheet"


def test_page_date_effective():
    md = "Rates are effective as of 08/31/2026 and subject to change"
    assert classify_page(PRODUCT, "", md, "2026-09-28").page_date == "2026-08-31"


def test_page_date_variants():
    f = lambda md: classify_page(PRODUCT, "", md, "2026-09-28").page_date
    assert f("APR effective 9/1/2026 is variable") == "2026-09-01"
    assert f("effective as of September 5, 2026.") == "2026-09-05"
    assert f("Offer effective September 2026") is None
    assert f("nothing here") is None
    assert f("effective marketing plan") is None


def test_page_date_first_match_wins():
    md = "Rates are effective as of 06/15/2026.\nRates are effective as of 03/03/2026."
    assert classify_page(PRODUCT, "", md, "2026-09-28").page_date == "2026-06-15"


def test_page_date_from_blog_url_and_news_text():
    assert classify_page("https://www.dupaco.com/2018/09/25/x/", "", "", "2026-09-28").page_date == "2018-09-25"
    md = "3 min read\n\n[A](u)\nJuly 14, 2023\n\n# Title"
    assert classify_page("https://www.dupaco.com/newsroom/x", "", md, "2026-09-28").page_date == "2023-07-14"


def test_invalid_date_is_none():
    assert classify_page(PRODUCT, "", "effective as of 13/45/2026", "2026-09-28").page_date is None


def test_authority_order():
    assert AUTHORITY["rate_sheet"] > AUTHORITY["product"] > AUTHORITY["calculator"] > AUTHORITY["news"] > AUTHORITY["legacy"]


@pytest.mark.real
def test_real_histogram():
    from conftest import real_snapshots
    snaps = real_snapshots()
    snap = [s for s in snaps if s.snapshot_id.startswith("05")][0]
    hist = Counter(classify_page(p.url, p.title, p.markdown, snap.taken_on).page_type for p in snap.pages)
    print("HIST", dict(hist))
    # No info.dupaco.com page exists in any real snapshot, so "legacy" is covered by test_url_types only.
    for t in PAGE_TYPES:
        if t not in ("promo", "legacy"):
            assert hist[t] >= 1, (t, hist)
    assert hist["other"] / sum(hist.values()) < 0.40, hist
