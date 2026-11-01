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
