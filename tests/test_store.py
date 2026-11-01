from conftest import mini_snapshots
from kb.model import Chunk, chunk_hash, FactObs, PageInfo, ResolvedFact
from kb.store import Store

S1, S2 = "01_2026-05-12_baseline", "02_2026-05-19_recrawl"
INFO = PageInfo("product", 80, None)


def mk(text, url="u1"):
    return Chunk(hash=chunk_hash(url, text), url=url, title="T", heading_path="A > B", text=text)


def store2():
    st = Store()
    st.add_snapshot(S1, "2026-05-12")
    st.add_snapshot(S2, "2026-05-19")
    return st


def test_chunk_dedup_across_snapshots():
    st = store2()
    c = mk("auto loan apr table")
    for sid in (S1, S2):
        pid = st.add_page(sid, "u1", "T", INFO, "ch")
        got = st.add_chunks(sid, pid, [c])
        assert got == [st.chunk(got[0].chunk_id)]
    assert len(st.new_chunks(S1)) == 1 and st.new_chunks(S2) == []
    n = st._db.execute("select count(*) from chunk").fetchone()[0]
    m = st._db.execute("select count(*) from chunk_snapshot").fetchone()[0]
    assert (n, m) == (1, 2)
    cid = st.new_chunks(S1)[0].chunk_id
    assert st.chunk_ids_in(S2) == {cid} and st.chunk(cid).chunk == c
    assert st.live_urls(S2) == {"u1"}


def test_fts_filters_by_snapshot():
    st = store2()
    p1 = st.add_page(S1, "u1", "T", INFO, "x")
    only1 = st.add_chunks(S1, p1, [mk("jumbo certificate special")])
    p2 = st.add_page(S2, "u2", "T", INFO, "y")
    st.add_chunks(S2, p2, [mk("auto loan rates", "u2")])
    assert st.fts_available
    assert [c for c, _ in st.fts_match("certificate", S1, 5)] == [only1[0].chunk_id]
    assert st.fts_match("certificate", S2, 5) == []
    assert st.fts_match('"auto" OR "loans"', S2, 5)[0][1] > 0  # porter stemming, positive score


def obs(**kw):
    d = dict(fact_key="auto_loan|apr|term=60", product="auto_loan", attribute="apr", qualifiers={"term": "60"},
             value_raw="5.39% APR1", value_norm="5.39%", snapshot_id=S1, taken_on="2026-05-12", url="u1",
             page_type="product", authority=80, page_date=None, quote="q", extractor="table", chunk_id=None)
    d.update(kw)
    return FactObs(**d)


def test_facts_roundtrip():
    st = store2()
    a, b = obs(), obs(snapshot_id=S2, taken_on="2026-05-19", value_norm="5.14%", chunk_id=3)
    c = obs(fact_key="share_savings|apy", product="share_savings", attribute="apy", qualifiers={})
    st.add_facts([b, a, c])
    assert st.facts(product="auto_loan") == [a, b]
    assert st.facts(fact_key=c.fact_key) == [c]
    assert st.facts(attribute="apy", snapshot_id=S1) == [c]
    assert st.fact_keys(S2) == ["auto_loan|apr|term=60"]
    assert st.fact_keys() == sorted([a.fact_key, c.fact_key])


def test_resolved_roundtrip():
    st = store2()
    w, l = obs(), obs(url="u2", value_norm="5.34%", authority=30)
    r = ResolvedFact(w.fact_key, "5.39%", w, [l], "authority")
    st.set_resolved(S1, [r])
    assert st.resolved(S1, "auto_loan", "apr") == [r]
    assert st.resolved(S1, "mortgage_fixed") == [] and st.resolved(S2) == []
    st.set_resolved(S1, [])
    assert st.resolved(S1) == []


def test_memo_roundtrip():
    st = store2()
    assert st.memo_get("k", S1) is None
    st.memo_put("k", S1, {"answer": "x", "n": [1]})
    assert st.memo_get("k", S1) == {"answer": "x", "n": [1]}
    assert st.memo_get("k", S2) is None


def test_mini_fixture_shapes():
    snaps = mini_snapshots()
    assert [s.snapshot_id[:2] for s in snaps] == ["01", "02", "05"]
    for s in snaps:
        assert len(s.pages) == 8
        assert any("60 Months" in p.markdown and "auto" in p.markdown.lower() for p in s.pages)


def test_same_text_different_url_is_two_chunks():
    st = store2()
    p = st.add_page(S1, "u1", "T", INFO, "x")
    a, b = st.add_chunks(S1, p, [mk("same text", "u1"), mk("same text", "u2")])
    assert a.chunk_id != b.chunk_id and st.chunk(b.chunk_id).chunk.url == "u2"
