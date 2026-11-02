import pytest

from kb.model import AUTHORITY, FactObs, Intent, PageInfo
from kb.resolve import resolve_snapshot, select_for_intent, stale_values
from kb.store import Store

DATES = {"s1": "2026-05-12", "s2": "2026-05-19", "s5": "2026-09-28"}


def obs(key, value, sid="s5", url="https://x/rates", ptype="rate_sheet", page_date=None, quote=""):
    from kb.keys import parse_key
    p, a, q = parse_key(key)
    return FactObs(key, p, a, q, value, value, sid, DATES[sid], url, ptype, AUTHORITY[ptype], page_date, quote, "table")


def build(facts, pages=None):
    st = Store(":memory:")
    for sid, d in DATES.items():
        st.add_snapshot(sid, d)
    urls = {(o.snapshot_id, o.url, o.page_type) for o in facts}
    for sid, url, ptype in urls | set(pages or []):
        st.add_page(sid, url, "t", PageInfo(ptype, AUTHORITY[ptype], None), "h")
    st.add_facts(facts)
    return st


def intent(product=None, attribute=None, q=None, family="fact", sup=None):
    return Intent(family, product, attribute, q or {}, sup, "", "c")


K = "auto_loan|apr|model_year=2023-2026"


def test_calculator_loses():
    st = build([obs(K, "5.39%", url="https://x/rates"), obs(K, "5.34%", url="https://x/calc", ptype="calculator")])
    [r] = resolve_snapshot(st, "s5")
    assert r.value == "5.39%" and r.rule == "authority:rate_sheet over calculator"
    assert [l.value_norm for l in r.losers] == ["5.34%"]


def test_dead_url_ignored():
    st = build([obs(K, "5.39%")])
    st.add_facts([obs("auto_loan|apr|model_year=2020-2020", "9%", url="https://x/gone")])
    st._db.execute("DELETE FROM page WHERE url='https://x/gone'")
    keys = [r.fact_key for r in resolve_snapshot(st, "s5")]
    assert keys == [K]


def test_as_of_snapshot():
    k = "certificate_special|apy|kind=jumbo;term=10"
    st = build([obs(k, "3.90%", "s2"), obs(k, "4.00%", "s5")])
    assert resolve_snapshot(st, "s2")[0].value == "3.90%"
    assert resolve_snapshot(st, "s5")[0].value == "4.00%"


def test_tier_boundaries():
    lo, hi = "share_savings|apy|tier=10000-24999", "share_savings|apy|tier=25000-inf"
    st = build([obs(lo, "0.10%"), obs(hi, "0.20%")])
    res = resolve_snapshot(st, "s5")
    f = lambda amt: [r.fact_key for r in select_for_intent(intent("share_savings", "apy", {"tier": amt}), res)]
    assert f("25000") == [hi]
    assert f("24999.99") == [lo]


def test_highest_tier():
    ks = [f"share_savings|apy|tier={b}" for b in ("0-999", "1000-24999", "25000-inf")]
    st = build([obs(ks[0], "0.05%"), obs(ks[1], "0.10%"), obs(ks[2], "0.20%"), obs(ks[2], "0.35%", "s1")])
    res = resolve_snapshot(st, "s5")
    sel = select_for_intent(intent("share_savings", "apy", sup="highest"), res)
    assert [r.value for r in sel] == ["0.20%"]
    assert [r.value for r in select_for_intent(intent("share_savings", "apy", sup="lowest"), res)] == ["0.05%"]


def test_mortgage_headline():
    def k(t, band):
        return f"mortgage_fixed|apr|loan_amount={band};term={t}"
    st = build([obs(k(360, "0-99999"), "7.50%"), obs(k(360, "100000-200000"), "7.13%"),
                obs(k(180, "100000-200000"), "6.25%"), obs(k(240, "100000-200000"), "6.60%"),
                obs(k(180, "200001-inf"), "6.00%")])
    sel = select_for_intent(intent("mortgage_fixed", "apr"), resolve_snapshot(st, "s5"))
    assert sorted(r.value for r in sel) == ["6.25%", "7.13%"]


def test_auto_newest_model_year():
    st = build([obs("auto_loan|apr|model_year=2020-2022", "6%"), obs(K, "5%")])
    sel = select_for_intent(intent("auto_loan", "apr"), resolve_snapshot(st, "s5"))
    assert [r.value for r in sel] == ["5%"]
    sel = select_for_intent(intent("auto_loan", "apr", {"model_year": "2021"}), resolve_snapshot(st, "s5"))
    assert [r.value for r in sel] == ["6%"]


def test_undated_award_question_selects_latest_year():
    """dup-087: 2023 release says "fourth", 2024 says "fifth"; an undated question must not state the stale one."""
    st = build([obs("org|award|name=forbes;year=2023", "fourth", ptype="news"),
                obs("org|award|name=forbes;year=2024", "fifth", url="https://x/news2", ptype="news")])
    res = resolve_snapshot(st, "s5")
    sel = select_for_intent(intent("org", "award", {"name": "forbes"}), res)
    assert [r.value for r in sel] == ["fifth"]
    sel = select_for_intent(intent("org", "award", {"name": "forbes", "year": "2023"}), res)
    assert [r.value for r in sel] == ["fourth"]


def test_year_holding_a_band_is_not_a_year():
    """LLM facts sometimes put a balance band in `year`; those keys are not pruned as older years."""
    ks = [f"money_market|apy|name=achieve;year={b}" for b in ("0.00-24999.99", "25000-99999.99")]
    st = build([obs(ks[0], "0.00%"), obs(ks[1], "3.66%")])
    sel = select_for_intent(intent("money_market", "apy", {"name": "achieve"}), resolve_snapshot(st, "s5"))
    assert sorted(r.value for r in sel) == ["0.00%", "3.66%"]


def test_missing_qualifier_no_match_and_exact():
    st = build([obs("certificate|apy|term=12", "4%"), obs("certificate|apy|term=24", "3%")])
    res = resolve_snapshot(st, "s5")
    assert [r.value for r in select_for_intent(intent("certificate", "apy", {"term": "24"}), res)] == ["3%"]
    assert select_for_intent(intent("certificate", "apy", {"segment": "business"}), res) == []


def test_family_caps():
    st = build([obs(f"certificate|apy|term={t}", f"{t}%") for t in (3, 6, 9, 12, 18)])
    res = resolve_snapshot(st, "s5")
    assert len(select_for_intent(intent("certificate", "apy"), res)) == 4
    assert len(select_for_intent(intent("certificate", "apy", family="list"), res)) == 5


def test_newer_page_date_and_later_observation():
    st = build([obs(K, "5.00%", url="https://x/a", ptype="product", page_date="2026-01-01"),
                obs(K, "5.10%", url="https://x/b", ptype="product", page_date="2026-03-01")])
    [r] = resolve_snapshot(st, "s5")
    assert r.value == "5.10%" and r.rule == "newer page date"
    # same authority, no dates: the value first seen in a later snapshot wins
    st = build([obs(K, "5.00%", "s1", url="https://x/a", ptype="product"),
                obs(K, "5.00%", "s5", url="https://x/a", ptype="product"),
                obs(K, "5.20%", "s5", url="https://x/b", ptype="product")])
    [r] = resolve_snapshot(st, "s5")
    assert r.value == "5.20%" and r.rule == "later observation"


def test_tie_sorted_by_url():
    st = build([obs(K, "5.00%", url="https://x/b", ptype="product"), obs(K, "5.10%", url="https://x/a", ptype="product")])
    [r] = resolve_snapshot(st, "s5")
    assert r.rule == "tie: higher authority" and r.winner.url == "https://x/a"


def test_news_and_expired_promo_dropped():
    st = build([obs(K, "5.00%", url="https://x/news", ptype="news"),
                obs(K, "4.00%", url="https://x/p", ptype="promo", quote="Special rate ends May 31, 2026"),
                obs(K, "5.50%", url="https://x/b", ptype="blog")])
    [r] = resolve_snapshot(st, "s5")
    assert r.value == "5.50%"
    # a promo that is still running beats a blog
    st = build([obs(K, "4.00%", url="https://x/p", ptype="promo", quote="ends Dec 31, 2026"),
                obs(K, "5.50%", url="https://x/b", ptype="blog")])
    assert resolve_snapshot(st, "s5")[0].value == "4.00%"
    # news alone is kept
    st = build([obs(K, "5.00%", url="https://x/news", ptype="news")])
    [r] = resolve_snapshot(st, "s5")
    assert r.value == "5.00%" and r.rule == "single source"


def test_stale_values():
    st = build([obs(K, "5.39%"), obs(K, "5.34%", url="https://x/calc", ptype="calculator"), obs(K, "5.10%", "s1")])
    res = resolve_snapshot(st, "s5")
    assert stale_values(st, res) == {"5.34%", "5.10%"}


def test_rule_not_tie_when_agreeing_winners():
    st = build([obs(K, "5.39%", url="https://x/a"), obs(K, "5.39%", url="https://x/b"),
                obs(K, "5.00%", url="https://x/n", ptype="news")])
    [r] = resolve_snapshot(st, "s5")
    assert r.value == "5.39%" and r.rule.startswith("authority")


def test_weekend_not_an_end_cue():
    st = build([obs(K, "4.00%", url="https://x/p", ptype="promo", quote="weekends 2020-01-01"),
                obs(K, "5.50%", url="https://x/b", ptype="blog")])
    assert resolve_snapshot(st, "s5")[0].value == "4.00%"


def test_nested_bands_narrowest():
    ks = ["mortgage_fixed|apr|loan_amount=0-100000;term=360", "mortgage_fixed|apr|loan_amount=0-200000;term=360"]
    res = resolve_snapshot(build([obs(ks[0], "7.13%"), obs(ks[1], "7.20%")]), "s5")
    f = lambda a: [r.fact_key for r in select_for_intent(intent("mortgage_fixed", "apr", {"loan_amount": a}), res)]
    assert f("150000") == [ks[1]]
    assert f("100000") == [ks[0]]


def test_mortgage_headline_prefers_purchase():
    b = "mortgage_fixed|apr|loan_amount=0-100000;term=360"
    r_ = "mortgage_fixed|apr|kind=refinance;loan_amount=0-100000;term=360"
    c = "mortgage_fixed|apr|loan_amount=0-200000;term=360"
    res = resolve_snapshot(build([obs(b, "7.13%"), obs(r_, "7.30%"), obs(c, "7.20%")]), "s5")
    assert [r.fact_key for r in select_for_intent(intent("mortgage_fixed", "apr"), res)] == [b]
    sel = select_for_intent(intent("mortgage_fixed", "apr", {"kind": "refinance"}), res)
    assert [r.fact_key for r in sel] == [r_]


# ---- Stage 2 final fix wave

def test_overlapping_model_year_bands_keep_highest_authority():
    """Real 05: 2023-2026 on /switch-and-earn (other) vs 2024-2027 on /rates both contain 2025 (dup-016/017)."""
    a = "auto_loan|apr|model_year=2023-2026;segment=personal;term=60"
    b = "auto_loan|apr|model_year=2024-2027;segment=personal;term=60"
    c = "auto_loan|apr|model_year=2021-2023;segment=personal;term=60"
    st = build([obs(a, "5.34%", url="https://x/switch-and-earn", ptype="other"), obs(b, "5.39%"), obs(c, "5.64%")])
    res = resolve_snapshot(st, "s5")
    for year in ("2025", "2024"):
        sel = select_for_intent(intent("auto_loan", "apr", {"segment": "personal", "term": "60", "model_year": year}), res)
        assert [r.value for r in sel] == ["5.39%"]
    # 2023: 2021-2023 and 2023-2026 both contain it (same width): the rate sheet wins
    sel = select_for_intent(intent("auto_loan", "apr", {"term": "60", "model_year": "2023"}), res)
    assert [r.value for r in sel] == ["5.64%"]


def test_rate_sheet_beats_narrower_band_on_other_page():
    """Real 05: 2021-22 5.59% on /switch-and-earn vs 2021-23 5.64% on /rates; a 2022 vehicle gets the rate sheet."""
    a = "auto_loan|apr|model_year=2021-2022;segment=personal;term=60"
    b = "auto_loan|apr|model_year=2021-2023;segment=personal;term=60"
    st = build([obs(a, "5.59%", url="https://x/switch-and-earn", ptype="other"), obs(b, "5.64%")])
    sel = select_for_intent(intent("auto_loan", "apr", {"term": "60", "model_year": "2022"}), resolve_snapshot(st, "s5"))
    assert [r.value for r in sel] == ["5.64%"]


def test_same_band_with_and_without_segment_collapse():
    """An LLM fact without `segment` and the table fact with it are one account; newer page date wins on equal authority."""
    k1 = "checking|apy|segment=personal;tier=5000-24999.99"
    k2 = "checking|apy|tier=5000-24999.99"
    k3 = "checking|apy|tier=0-4999.99"
    k4 = "checking|apy|segment=personal;tier=0.01-4999.99"
    st = build([obs(k1, "2.50%", page_date="2026-01-01"), obs(k2, "3.00%", url="https://x/b", page_date="2026-06-01"),
                obs(k3, "0.00%", url="https://x/b"), obs(k4, "0.00%")])
    sel = select_for_intent(intent("checking", "apy", family="list"), resolve_snapshot(st, "s5"))
    assert sorted(r.fact_key for r in sel) == sorted([k2, k4])  # 0.01-4999.99 is the narrower band
    sel = select_for_intent(intent("checking", "apy", {"tier": "6000"}), resolve_snapshot(st, "s5"))
    assert [r.value for r in sel] == ["3.00%"]


def test_named_account_not_merged_with_plain_one():
    """Achieve Money Market and the plain money market share the 25000-99999.99 band but are different accounts."""
    ach = "money_market|apy|name=achieve;segment=personal;tier=25000-99999.99"
    mm = "money_market|apy|segment=personal;tier=25000-99999.99"
    st = build([obs(ach, "3.66%", url="https://x/earn-more", ptype="other"), obs(mm, "0.35%")])
    res = resolve_snapshot(st, "s5")
    assert sorted(r.value for r in select_for_intent(intent("money_market", "apy", {"tier": "50000"}), res)) == ["0.35%", "3.66%"]


def test_distinct_kinds_and_tiers_not_grouped():
    reg = "certificate_special|apy|segment=personal;term=17;tier=1000-inf"
    jumbo = "certificate_special|apy|kind=jumbo;segment=personal;term=17;tier=100000-inf"
    st = build([obs(reg, "4.10%"), obs(jumbo, "4.20%")])
    sel = select_for_intent(intent("certificate_special", "apy", {"term": "17"}), resolve_snapshot(st, "s5"))
    assert sorted(r.value for r in sel) == ["4.10%", "4.20%"]
    tiers = [f"share_savings|apy|tier={b}" for b in ("0-99.99", "100-2499.99", "2500-9999.99")]
    st = build([obs(k, f"0.{i}0%") for i, k in enumerate(tiers)] + [obs("share_savings|apy|", "0.05%", url="https://x/p")])
    assert len(select_for_intent(intent("share_savings", "apy", family="list"), resolve_snapshot(st, "s5"))) == 4


def test_band_order_numeric_before_cap():
    ks = [f"share_savings|apy|tier={b}" for b in ("100000-inf", "25000-99999.99", "5000-24999.99", "0-4999.99", "1000000-inf")]
    st = build([obs(k, f"0.{i}0%") for i, k in enumerate(ks)])
    sel = select_for_intent(intent("share_savings", "apy"), resolve_snapshot(st, "s5"))
    assert [r.fact_key.split("tier=")[1] for r in sel] == ["0-4999.99", "5000-24999.99", "25000-99999.99", "100000-inf"]
    terms = [f"certificate|apy|term={t}" for t in (12, 6, 24, 3, 60)]
    st = build([obs(k, "1.00%") for k in terms])
    sel = select_for_intent(intent("certificate", "apy"), resolve_snapshot(st, "s5"))
    assert [r.fact_key.split("term=")[1] for r in sel] == ["3", "6", "12", "24"]


def test_generic_rate_selects_rate_and_apy_or_apr():
    """attribute None + product = a generic "rate" question (dup-050, dup-040): deposits rate+apy, loans rate+apr."""
    st = build([obs("bright_savings|apy|tier=0.01-1000000", "2.25%"), obs("bright_savings|rate|tier=0.01-1000000", "2.23%"),
                obs("bright_savings|min_balance|", "$0"),
                obs("credit_card|apr|name=platinum-visa", "12.50% - 19.50%"), obs("credit_card|rate|name=x", "3.00%"),
                obs("credit_card|apy|name=y", "1.00%")])
    res = resolve_snapshot(st, "s5")
    sel = select_for_intent(intent("bright_savings", None), res)
    assert [(r.fact_key.split("|")[1], r.value) for r in sel] == [("apy", "2.25%"), ("rate", "2.23%")]
    sel = select_for_intent(intent("credit_card", None, family="list"), res)
    assert sorted(r.value for r in sel) == ["12.50% - 19.50%", "3.00%"]
    # explicit attribute stays exact
    assert [r.value for r in select_for_intent(intent("bright_savings", "rate"), res)] == ["2.23%"]


def test_generic_rate_cap_counts_accounts_not_attributes():
    st = build([obs(f"certificate|{a}|term={t}", f"{t}.0{i}%") for t in (3, 6, 9, 12, 18) for i, a in enumerate(("apy", "rate"))])
    sel = select_for_intent(intent("certificate", None), resolve_snapshot(st, "s5"))
    assert len(sel) == 8 and [r.fact_key.split("|")[1] for r in sel[:2]] == ["apy", "rate"]


def test_superlative_on_range_uses_low_end():
    st = build([obs("credit_card|apr|name=platinum-visa", "12.50% - 19.50%"), obs("credit_card|apr|name=rewards-visa", "15.50% - 21.00%")])
    sel = select_for_intent(intent("credit_card", "apr", sup="lowest"), resolve_snapshot(st, "s5"))
    assert [r.value for r in sel] == ["12.50% - 19.50%"]


def test_branch_name_token_subset():
    k1 = "branch|hours_lobby|day=mon-fri;name=madison-mineral-point-road"
    k2 = "branch|hours_lobby|day=mon-fri;name=dubuque-mullan-ave"
    st = build([obs(k1, "9 a.m. - 5 p.m."), obs(k2, "8 a.m. - 5 p.m.")])
    res = resolve_snapshot(st, "s5")
    f = lambda n: [r.fact_key for r in select_for_intent(intent("branch", "hours_lobby", {"name": n}), res)]
    assert f("mineral-point-road") == [k1]
    assert f("mullan-avenue") == [k2]
    assert f("madison") == [k1]
    assert f("key-west") == []
    # other qualifiers stay exact
    assert [r.fact_key for r in select_for_intent(intent("branch", "hours_lobby", {"name": "madison", "day": "sat"}), res)] == []
