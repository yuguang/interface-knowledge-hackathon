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
