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
