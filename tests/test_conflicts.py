import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))

from kb.conflicts import build_conflicts
from kb.keys import make_key, parse_key
from kb.model import FactObs, PageInfo, ResolvedFact
from kb.snapshots import SnapshotRegistry
from kb.store import Store
from scoring import score_conflicts

SNAPS = [("01_2026-05-12_baseline", "2026-05-12"), ("02_2026-05-19_recrawl", "2026-05-19"),
         ("03_2026-06-30_x", "2026-06-30"), ("04_2026-08-15_x", "2026-08-15"), ("05_2026-09-28_x", "2026-09-28")]


def setup():
    st, reg = Store(":memory:"), SnapshotRegistry()
    for sid, d in SNAPS:
        st.add_snapshot(sid, d)
        reg.add(sid, d)
    return st, reg


def ob(key, val, sid, url="https://d.org/rates", ptype="rate_sheet", auth=100, ex="table"):
    p, a, q = parse_key(key)
    d = dict(SNAPS)
    return FactObs(key, p, a, q, val, val, sid, d[sid], url, ptype, auth, None, f"q {val}", ex)


def resolve(st, sid, key, winner, losers=(), rule="single source"):
    st.set_resolved(sid, [ResolvedFact(key, winner.value_norm, winner, list(losers), rule)])


AUTO = make_key("auto_loan", "apr", {"term": "60", "model_year": "2024-2027", "kind": "personal"})


def test_one_entry_per_key():
    st, reg = setup()
    plan = {0: "3.90%", 1: "3.90%", 2: "3.95%", 3: "4.00%", 4: "4.00%"}
    for i, v in plan.items():
        st.add_facts([ob(AUTO, v, SNAPS[i][0])])
    resolve(st, SNAPS[4][0], AUTO, ob(AUTO, "4.00%", SNAPS[4][0]))
    out = build_conflicts(st, reg)
    assert len(out) == 1
    c = out[0]
    assert c.values == ["3.90%", "3.95%", "4.00%"] and c.resolution == "4.00%"
    assert c.reason == "updated in 04_2026-08-15_x"
    assert len(c.sources) == 3
    assert c.topic == "60-month auto loan APR 2024-2027 model year personal"


def test_within_snapshot():
    st, reg = setup()
    k = make_key("mortgage_fixed", "apr", {"term": "360"})
    s5 = SNAPS[4][0]
    good = ob(k, "5.39%", s5)
    bad = ob(k, "5.34%", s5, url="https://d.org/calc", ptype="calculator", auth=30)
    st.add_facts([good, bad])
    resolve(st, s5, k, good, [bad], "authority:rate_sheet over calculator")
    (c,) = build_conflicts(st, reg)
    assert c.resolution == "5.39%" and "calculator" in c.reason
    assert set(c.values) == {"5.39%", "5.34%"}
    assert c.topic == "30-year fixed mortgage APR"


def test_different_tiers_not_conflict():
    st, reg = setup()
    s = SNAPS[4][0]
    st.add_facts([ob(make_key("certificate", "apy", {"term": "12", "tier": "10000-24999"}), "3.00%", s),
                  ob(make_key("certificate", "apy", {"term": "12", "tier": "25000-inf"}), "3.50%", s)])
    assert build_conflicts(st, reg) == []


def test_filters():
    st, reg = setup()
    k = make_key("certificate", "apy", {"term": "6"})
    st.add_facts([ob(k, "3.00%", SNAPS[0][0]), ob(k, "3.10%", SNAPS[1][0], ex="llm:v1")])
    d = make_key("program", "detail", {"name": "x"})
    st.add_facts([ob(d, "a", SNAPS[0][0]), ob(d, "b", SNAPS[1][0])])
    assert build_conflicts(st, reg) == []


def test_scores_against_kit_scorer():
    st, reg = setup()
    k1 = make_key("certificate", "apy", {"term": "6"})
    k2 = make_key("certificate_special", "apy", {"term": "10", "segment": "jumbo"})
    for k, a, b in ((k1, "2.95%", "3.50%"), (k2, "4.10%", "4.40%")):
        st.add_facts([ob(k, a, SNAPS[0][0]), ob(k, b, SNAPS[4][0])])
    st.set_resolved(SNAPS[4][0], [ResolvedFact(k1, "3.50%", ob(k1, "3.50%", SNAPS[4][0]), [], "single source"),
                                  ResolvedFact(k2, "4.40%", ob(k2, "4.40%", SNAPS[4][0]), [], "single source")])
    gold = [{"id": "c1", "topic_keywords": ["6-month", "certificate"], "values": ["2.95%", "3.50%"], "correct_value": "3.50%"},
            {"id": "c2", "topic_keywords": ["10-month", "jumbo"], "values": ["4.10%", "4.40%"], "correct_value": "4.40%"}]
    out = build_conflicts(st, reg)
    assert [c.topic for c in out] == ["10-month jumbo certificate special APY", "6-month certificate APY"]
    r = score_conflicts(gold, out)
    assert r["f1"] == 1.0 and r["resolution_accuracy"] == 1.0


# --- Task 3.3 precision filters ---------------------------------------------------------------

S1, S2, S3, S4, S5 = (s for s, _ in SNAPS)


def test_values_on_one_page_are_rows_not_a_conflict():
    """Three values for one key on the same page in the same snapshot: a tiered table whose
    distinguishing qualifier was not read (e.g. RV model years), never a disagreement."""
    st, reg = setup()
    k = make_key("rv_loan", "apr", {"term": "36"})
    st.add_facts([ob(k, v, S5) for v in ("8.34%", "8.54%", "8.89%")]
                 + [ob(k, v, S5, url="https://d.org/rv", ptype="product", auth=80) for v in ("8.34%", "8.54%", "8.89%")])
    assert build_conflicts(st, reg) == []


def test_co_occurring_pair_ignored_but_real_change_kept():
    st, reg = setup()
    k = make_key("credit_card", "apr", {"name": "platinum-visa"})
    st.add_facts([ob(k, "12.50% - 19.50%", S1), ob(k, "15.50%", S1),       # two rows of one table
                  ob(k, "12.50% - 19.50%", S5)])
    assert build_conflicts(st, reg) == []
    st.add_facts([ob(k, "13.00% - 20.00%", S1, url="https://d.org/cards", ptype="product", auth=80)])
    (c,) = build_conflicts(st, reg)
    assert sorted(c.values) == ["12.50% - 19.50%", "13.00% - 20.00%", "15.50%"]
    assert c.resolution == "12.50% - 19.50%"


def test_list_attribute_items_are_not_conflicts():
    st, reg = setup()
    k = make_key("checking", "eligibility", {"segment": "personal"})
    st.add_facts([ob(k, "Must be 18", S1, ex="regex"), ob(k, "Must be a member", S2, ex="regex"),
                  ob(k, "Must be 18", S2, url="https://d.org/x", ex="regex")])
    assert build_conflicts(st, reg) == []


def test_prose_values_compared_by_the_figures_they_state():
    st, reg = setup()
    k = make_key("program", "count", {"name": "scholarship"})
    st.add_facts([ob(k, "twenty renewable scholarships", S1, url="https://d.org/a"),
                  ob(k, "twenty renewable scholarships for the 2026-2027 academic year", S5, url="https://d.org/b")])
    assert build_conflicts(st, reg) == []
    k2 = make_key("program", "award", {"name": "grant"})
    st.add_facts([ob(k2, "grants up to $40,000", S1, url="https://d.org/a"),
                  ob(k2, "grants up to $50,000", S5, url="https://d.org/a")])
    (c,) = build_conflicts(st, reg)
    assert c.values == ["grants up to $40,000", "grants up to $50,000"] and c.resolution == "grants up to $50,000"


def test_yearly_keys_merge_into_one_conflict_resolved_to_latest_year():
    st, reg = setup()
    k23 = make_key("org", "award", {"name": "forbes", "year": "2023"})
    k24 = make_key("org", "award", {"name": "forbes", "year": "2024"})
    o23 = ob(k23, "fourth", S5, url="https://d.org/news/2023", ptype="news", auth=10)
    o24 = ob(k24, "fifth", S5, url="https://d.org/news/2024", ptype="news", auth=10)
    st.add_facts([o23, o24])
    st.set_resolved(S5, [ResolvedFact(k23, "fourth", o23, [], "single source"),
                         ResolvedFact(k24, "fifth", o24, [], "single source")])
    (c,) = build_conflicts(st, reg)
    assert c.values == ["fourth", "fifth"] and c.resolution == "fifth"
    assert c.topic == "organization award forbes" and c.reason == "most recent year (2024)"


def _auto(my, term="60"):
    return make_key("auto_loan", "apr", {"term": term, "model_year": my, "segment": "personal"})


def test_relabelled_model_year_bucket_is_one_conflict():
    """The site relabels "2023-26" as "2024-27": same row, so the old and new values form one
    conflict whose winner is the new bucket's rate-sheet value, not a stale page's old bucket."""
    st, reg = setup()
    old, new, mid_old, mid_new = _auto("2023-2026"), _auto("2024-2027"), _auto("2021-2022"), _auto("2021-2023")
    stale = dict(url="https://d.org/promo", ptype="other", auth=40)
    for sid in (S1, S2, S3, S4):
        st.add_facts([ob(old, "5.19%", sid), ob(mid_old, "5.44%", sid)])
    st.add_facts([ob(new, "5.39%", S5), ob(mid_new, "5.64%", S5), ob(old, "5.34%", S5, **stale)])
    st.set_resolved(S5, [ResolvedFact(new, "5.39%", ob(new, "5.39%", S5), [], "single source"),
                         ResolvedFact(mid_new, "5.64%", ob(mid_new, "5.64%", S5), [], "single source"),
                         ResolvedFact(old, "5.34%", ob(old, "5.34%", S5, **stale), [], "single source")])
    out = {c.resolution: c for c in build_conflicts(st, reg)}
    assert set(out) == {"5.39%", "5.64%"}  # 2021-2023 pairs with 2021-2022 (best overlap), not 2023-2026
    assert out["5.39%"].values == ["5.19%", "5.34%", "5.39%"]
    assert out["5.64%"].values == ["5.44%", "5.64%"]
    assert "2024-2027" in out["5.39%"].topic


def test_model_year_buckets_on_one_page_are_not_merged():
    st, reg = setup()
    a, b = _auto("2023-2026"), _auto("2024-2027")
    st.add_facts([ob(a, "5.19%", S5), ob(b, "5.39%", S5)])  # both rows on the same rate sheet
    assert build_conflicts(st, reg) == []


def test_rate_shadowed_by_apy_and_apr_by_rate():
    st, reg = setup()
    q = {"term": "12", "tier": "1000-inf", "segment": "personal"}
    for attr, a, b in (("apy", "2.90%", "3.40%"), ("rate", "2.88%", "3.37%")):
        k = make_key("certificate", attr, q)
        st.add_facts([ob(k, a, S1), ob(k, b, S5)])
    m = {"term": "180", "segment": "personal"}
    for attr, a, b in (("rate", "5.625%", "6.75%"), ("apr", "5.749%", "6.88%")):
        k = make_key("mortgage_fixed", attr, m)
        st.add_facts([ob(k, a, S1), ob(k, b, S5)])
    topics = sorted(c.topic for c in build_conflicts(st, reg))
    assert topics == ["12-month personal certificate APY $1,000 and up", "15-year personal fixed mortgage rate"]


def test_mirrored_rows_collapse_to_the_plainest_label():
    """The same row under business/personal or IRA/certificate labels is one change; a different
    tier with the same history is a different fact and stays."""
    st, reg = setup()
    row = {"term": "10", "tier": "1000-inf"}
    keys_ = [make_key("certificate_special", "apy", {**row, "segment": "business"}),
             make_key("certificate_special", "apy", {**row, "segment": "personal"}),
             make_key("ira", "apy", {**row, "segment": "personal", "name": "roth"}),
             make_key("certificate_special", "apy", {"term": "10", "tier": "100000-inf", "segment": "personal"})]
    for k in keys_:
        st.add_facts([ob(k, "3.80%", S1), ob(k, "3.90%", S5)])
    st.set_resolved(S5, [ResolvedFact(k, "3.90%", ob(k, "3.90%", S5), [], "single source") for k in keys_])
    out = build_conflicts(st, reg)
    assert sorted(c.topic for c in out) == ["10-month personal certificate special APY $1,000 and up",
                                           "10-month personal certificate special APY $100,000 and up"]


def test_purchase_and_refinance_nest_into_one():
    st, reg = setup()
    p = make_key("mortgage_fixed", "rate", {"term": "180", "segment": "personal"})
    r = make_key("mortgage_fixed", "rate", {"term": "180", "segment": "personal", "kind": "refinance"})
    st.add_facts([ob(p, "5.625%", S1), ob(p, "6.75%", S5),
                  ob(r, "5.625%", S1), ob(r, "5.75%", S2), ob(r, "6.75%", S5)])
    st.set_resolved(S5, [ResolvedFact(p, "6.75%", ob(p, "6.75%", S5), [], "single source"),
                         ResolvedFact(r, "6.75%", ob(r, "6.75%", S5), [], "single source")])
    (c,) = build_conflicts(st, reg)
    assert c.topic == "15-year personal fixed mortgage rate" and c.values == ["5.625%", "6.75%"]
