import pytest

from kb.keys import (band_contains, make_key, norm_band, norm_model_year, norm_money,
                     norm_percent, norm_term_months, parse_key)


def test_make_key_sorted():
    assert make_key("auto_loan", "apr", {"term": "60", "model_year": "2024-2027"}) == "auto_loan|apr|model_year=2024-2027;term=60"


def test_parse_roundtrip():
    assert parse_key("auto_loan|apr|model_year=2024-2027;term=60") == ("auto_loan", "apr", {"model_year": "2024-2027", "term": "60"})


def test_parse_no_qualifiers():
    assert parse_key(make_key("org", "count", {})) == ("org", "count", {})


def test_unknown_qualifier():
    with pytest.raises(ValueError):
        make_key("auto_loan", "apr", {"color": "red"})


@pytest.mark.parametrize("s,v", [("5.390 %", "5.39%"), ("5.39% APR1", "5.39%"), ("4.00% APY", "4.00%"), ("0.15", "0.15%"), ("n/a", None)])
def test_norm_percent(s, v):
    assert norm_percent(s) == v


@pytest.mark.parametrize("s,v", [("$15,000.00", "$15,000"), ("$2.50", "$2.50"), ("none", None)])
def test_norm_money(s, v):
    assert norm_money(s) == v


def test_norm_band():
    assert norm_band("$10,000 - $24,999") == "10000-24999" and norm_band("$25,000+") == "25000-inf"
    assert norm_band("up to $100,000") == "0-100000"


def test_norm_band_dollar_only_cents_and_reversed():
    assert norm_band("Tier 1: $0 - $499") == "0-499"
    assert norm_band("$10,000 - $24,999.99") == "10000-24999.99"
    assert band_contains("10000-24999.99", 24999.5)
    assert norm_band("$10,000 - $24,999") == "10000-24999"
    assert norm_band("$500 - $100") is None


def test_norm_term():
    assert norm_term_months("60 Months") == 60 and norm_term_months("5 years") == 60 and norm_term_months("17-month") == 17


def test_model_year():
    assert norm_model_year("2024-27 Model Year") == "2024-2027"
    assert norm_model_year("2016 Model Year (and older)") == "0-2016"


def test_band_contains():
    assert band_contains("10000-24999", 24999) and not band_contains("10000-24999", 25000) and band_contains("25000-inf", 10**7)
    assert band_contains("10000-24999", 10000)


# --- F2 (R11, R12, R17) ---

@pytest.mark.parametrize("s,v", [("2023–26 Model Year", "2023-2026"), ("2023—26 Model Year", "2023-2026"),
                                 ("2021 to 2023 Model Year", "2021-2023"), ("2017-20 Model Year", "2017-2020")])
def test_model_year_separators(s, v):
    assert norm_model_year(s) == v


def test_model_year_single_is_point_band():
    assert norm_model_year("2020 Model Year") == "2020-2020"
    assert norm_model_year("2016 Model Year (and older)") == "0-2016"


@pytest.mark.parametrize("s,v", [("$2,500 – $9,999.99", "2500-9999.99"), ("$2,500 — $9,999", "2500-9999"),
                                 ("$2,500 to $9,999", "2500-9999"), ("$25,000-39,999", "25000-39999")])
def test_norm_band_separators(s, v):
    assert norm_band(s) == v


@pytest.mark.parametrize("s", ["", "abc", "10000", "x-y", "10000-", None])
def test_band_contains_malformed_is_false(s):
    assert band_contains(s, 5) is False


def test_band_gap_rule():
    assert band_contains("10000-24999", 24999.99) is True
    assert band_contains("10000-24999", 25000) is False
    assert band_contains("10000-24999.99", 24999.99) is True
    assert band_contains("10000-24999.99", 24999.995) is False


def test_norm_band_minimum():
    assert norm_band("$100,000", minimum=True) == "100000-inf"
    assert norm_band("$1,000.00", minimum=True) == "1000-inf"
    assert norm_band("$1,000.00") is None  # default unchanged
    assert norm_band("$10,000 - $24,999", minimum=True) == "10000-24999"
    assert norm_band("up to $100,000", minimum=True) == "0-100000"


def test_norm_band_no_dollar_uses_separator_range():
    assert norm_band("Tier 1: 0 - 499") == "0-499"
    assert norm_band("Tier 2: 500 to 999") == "500-999"


@pytest.mark.parametrize("s", ["36-72 months", "1-5 years", "36 – 72 Months", "12 to 24 months"])
def test_norm_term_range_is_none(s):
    assert norm_term_months(s) is None


@pytest.mark.parametrize("s,v", [("4%", "4.00%"), (".25%", "0.25%"), ("4.5%", "4.50%"), ("5.125%", "5.125%"),
                                 ("0.250%", "0.25%"), ("4", "4.00%")])
def test_norm_percent_two_decimals(s, v):
    assert norm_percent(s) == v


@pytest.mark.parametrize("p,a,q", [("boat_loan", "apr", {}), ("auto_loan", "colour", {}),
                                   ("auto_loan", "apr", {"term": "60;x"}), ("auto_loan", "apr", {"term": "a=b"}),
                                   ("auto_loan", "apr", {"name": "a|b"})])
def test_make_key_rejects_bad_parts(p, a, q):
    with pytest.raises(ValueError):
        make_key(p, a, q)


# ---- Task 3.2a: every qualifier normaliser is idempotent (F10)

_QUAL_SAMPLES = [
    ("model_year", v) for v in ["2019 and older", "2023–26 Model Year", "2020 Model Year", "2024 or newer",
                                "2021 and later", "2018 or earlier", "2023-2026", "0-2019", "2024-9999"]
] + [("term", v) for v in ["60", "5 years", "36 months", "72 mo", "1 year"]
] + [(k, v) for k in ("tier", "loan_amount") for v in [
    "$10,000 - $24,999", "$25,000+", "up to $100,000", "$100,000 or less", "0-100000", "25000-inf",
    "10000-24999.99", "$1,000 and over", "100000"]
] + [("name", v) for v in ["Hy-Vee", "Asbury Road", "asbury-road", "Madison, Mineral Point Rd."]
] + [("segment", "Business"), ("segment", "personal")]


@pytest.mark.parametrize("k,v", _QUAL_SAMPLES)
def test_qualifier_normalisers_idempotent(k, v):
    from kb.extract.llm_facts import _norm_quals
    once = _norm_quals("auto_loan", {k: v})
    if once is None:
        return
    assert _norm_quals("auto_loan", dict(once)) == once


@pytest.mark.parametrize("s", ["2019 and older", "2024 or newer", "2023–26", "2020"])
def test_norm_model_year_idempotent(s):
    once = norm_model_year(s)
    assert norm_model_year(once) == once


@pytest.mark.parametrize("s", ["$25,000+", "up to $100,000", "$10,000 - $24,999", "0-100000", "25000-inf"])
def test_norm_band_idempotent(s):
    once = norm_band(s, minimum=True)
    assert norm_band(once, minimum=True) == once
