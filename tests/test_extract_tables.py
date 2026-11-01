import json
from pathlib import Path

import pytest

from kb import keys
from kb.extract.tables import extract
from kb.model import Chunk, PageContext, PageInfo, StoredChunk, chunk_hash

REAL = Path(__file__).resolve().parent.parent / "snapshots"


def ctx(md, url="https://www.dupaco.com/personal/rates", title="Rates", chunks=()):
    info = PageInfo("rate_sheet", 100, None)
    return PageContext("05_x", "2026-09-28", url, title, info, md, list(chunks))


def by_key(obs):
    return {o.fact_key: o for o in obs}


AUTO = ("## Rates\n| Loan Program | Term | Annual Percentage Rate (As Low As) |\n| --- | --- | --- |\n"
        "| 2024-27 Model Year | 60 Months | 5.39% APR1 |\n| 2016 Model Year (and older) | 48 Months | 8.59% APR1 |")
AUTO_URL = "https://www.dupaco.com/personal/loans/auto-loans"


def test_auto_table():
    f = by_key(extract(ctx(AUTO, url=AUTO_URL)))
    o = f["auto_loan|apr|model_year=2024-2027;segment=personal;term=60"]
    assert o.value_norm == "5.39%" and o.extractor == "table" and o.value_raw == "5.39% APR1"
    assert o.snapshot_id == "05_x" and o.page_type == "rate_sheet" and o.authority == 100
    assert o.url == AUTO_URL and len(o.quote) <= 200 and "5.39%" in o.quote
    assert f["auto_loan|apr|model_year=0-2016;segment=personal;term=48"].value_norm == "8.59%"


def test_footnote_suffix_stripped():
    md = AUTO.replace("5.39% APR1", "5.390 %1")
    obs = extract(ctx(md, url=AUTO_URL))
    assert obs and all(o.value_norm.endswith("%") and "APR" not in o.value_norm for o in obs)
    assert any(o.value_norm == "5.39%" for o in obs)


def test_savings_tiers():
    md = ("| Account Type | Balance Tier | Dividend Rate | APY |\n| --- | --- | --- | --- |\n"
          "| Share Savings | $10,000 - $24,999 | 0.15% | 0.15%1 |\n| Share Savings | $25,000+ | 0.20% | 0.20% |")
    f = by_key(extract(ctx(md)))
    assert f["share_savings|apy|segment=personal;tier=10000-24999"].value_norm == "0.15%"
    assert f["share_savings|rate|segment=personal;tier=10000-24999"].value_norm == "0.15%"
    assert f["share_savings|apy|segment=personal;tier=25000-inf"].value_norm == "0.20%"


def test_business_segment():
    md = "| Account Type | Balance Tier | APY |\n| --- | --- | --- |\n| Share Savings | $0 - $99 | 0.10% |"
    f = by_key(extract(ctx(md, url="https://www.dupaco.com/business/rates")))
    assert "share_savings|apy|segment=business;tier=0-99" in f


def test_jumbo_special():
    md = ("| Certificate | Minimum Deposit | APY |\n| --- | --- | --- |\n"
          "| 17-Month Fixed Jumbo Special | $100,000 | 4.20% APY |\n"
          "| 17-Month Fixed Special | $1,000 | 4.10% APY |")
    f = by_key(extract(ctx(md)))
    j = f["certificate_special|apy|kind=jumbo;segment=personal;term=17;tier=100000-inf"]
    assert j.value_norm == "4.20%"
    assert f["certificate_special|apy|segment=personal;term=17;tier=1000-inf"].value_norm == "4.10%"


def test_mortgage_program_rows():
    md = ("Home Loans - Refinance\n\n| Program | Rate | Points | APR | Payment |\n| --- | --- | --- | --- | --- |\n"
          "| Refinance: 30-Year Fixed Conventional $100,000 or less | 7.13% | 0 | 7.28% | $673.80 |\n"
          "| Refinance: 15-Year Fixed Conventional | 6.75% | 0 | 6.88% | $1,327.36 |")
    f = by_key(extract(ctx(md)))
    k = "mortgage_fixed|rate|kind=refinance;loan_amount=0-100000;segment=personal;term=360"
    assert f[k].value_norm == "7.13%"
    assert f[k.replace("|rate|", "|apr|")].value_norm == "7.28%"
    assert f["mortgage_fixed|rate|kind=refinance;segment=personal;term=180"].value_norm == "6.75%"
    assert not any("673" in o.value_norm for o in f.values())


def test_amortisation_table_ignored():
    md = "| Category | Interest | Principal | Remaining |\n| --- | --- | --- | --- |\n| 1 | $0 | $0 | $25,000 |"
    assert extract(ctx(md, url=AUTO_URL)) == []


def test_unknown_product_and_range_rows_skipped_without_raising():
    md = ("| Thing | Rate |\n| --- | --- |\n| Mystery | 1.00% |\n")
    assert extract(ctx(md, url="https://www.dupaco.com/about")) == []
    md = "| Card Type | APR |\n| --- | --- |\n| Platinum Visa Rate | 12.50% – 19.50%2 |\n| Intro | 2.90%1 |"
    f = by_key(extract(ctx(md)))
    assert [o.value_norm for o in f.values()] == ["2.90%"]


def test_chunk_id_and_term_range_row():
    row = "| 2024-27 Model Year | 60 Months | 5.39% APR1 |"
    c = Chunk(chunk_hash("u", AUTO), "u", "t", "Rates", AUTO)
    obs = extract(ctx(AUTO, url=AUTO_URL, chunks=[StoredChunk(7, c)]))
    assert row in AUTO and all(o.chunk_id == 7 for o in obs)
    md = AUTO + "\n| 2020 Model Year | 36-72 Months | 6.00% APR |"
    assert not any("6.00%" == o.value_norm for o in extract(ctx(md, url=AUTO_URL)))


@pytest.mark.real
def test_real_known_values():
    d = REAL / "05_2026-09-28_latest"
    if not d.exists():
        pytest.skip("no snapshots")
    man = json.loads((d / "manifest.json").read_text())
    pages = {p["url"]: p for p in man["pages"]}

    def run(url_part):
        p = next(p for u, p in pages.items() if u.rstrip("/").endswith(url_part))
        return by_key(extract(ctx((d / p["file"]).read_text(encoding="utf-8"), url=p["url"], title=p["title"])))

    auto = run("/personal/loans/auto-loans")
    assert auto["auto_loan|apr|model_year=2024-2027;segment=personal;term=60"].value_norm == "5.39%"
    home = run("/personal/loans/home-loans")
    k = "mortgage_fixed|rate|loan_amount=0-100000;segment=personal;term=360"
    assert home[k].value_norm == "7.13%"
    rates = run("/rates")
    assert rates["share_savings|apy|segment=personal;tier=10000-24999.99"].value_norm == "0.15%"
    for o in rates.values():
        keys.parse_key(o.fact_key)


def test_term_column_holding_program_description():
    md = ("Home Loan Refinance\n\n| Term | Rate | Points | APR |\n| --- | --- | --- | --- |\n"
          "| Refinance: 30-Year Fixed Conventional $200000 or less | 5.875% | 0.00% | 5.966% |")
    f = by_key(extract(ctx(md, url="https://www.dupaco.com/mortgage-refi")))
    assert f["mortgage_fixed|rate|kind=refinance;loan_amount=0-200000;segment=personal;term=360"].value_norm == "5.875%"


def test_unmapped_named_account_skipped():
    md = "| Account Type | Balance Tier | APY |\n| --- | --- | --- |\n| Launchpad Savings | $5 - $1,000 | 3.25% |"
    assert extract(ctx(md, url="https://www.dupaco.com/personal/retirement")) == []
