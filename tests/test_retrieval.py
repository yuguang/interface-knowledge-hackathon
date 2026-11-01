import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import pytest
from chromadb import EmbeddingFunction

from conftest import ALL5, ROOT, real_snapshots
from kb import normalize
from kb.llm import LLM
from kb.model import Chunk, Hit, PageInfo, chunk_hash
from kb.retrieval import dense, fts, picker
from kb.retrieval.fts import Bm25Fallback, fts_search, to_match_expr
from kb.retrieval.picker import pick_sections
from kb.retrieval.pipeline import Retriever
from kb.snapshots import SnapshotRegistry, effective_as_of
from kb.store import Store

S1, S2, S5 = "01_2026-05-12_baseline", "02_2026-05-19_recrawl", "05_2026-09-28_latest"
INFO = PageInfo("product", 80, None)


class HashEmbedder(EmbeddingFunction):
    """Deterministic bag-of-words embedder; counts the texts it embeds."""

    def __init__(self):
        self.calls = 0

    @staticmethod
    def name():
        return "test-hash"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return HashEmbedder()

    def __call__(self, input):
        self.calls += len(input)
        out = []
        for t in input:
            v = [0.0] * 64
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture
def embedder(monkeypatch):
    e = HashEmbedder()
    monkeypatch.setattr(dense, "embedding_function", lambda: e)
    return e


def _add(st, sid, url, texts):
    pid = st.add_page(sid, url, "T", INFO, "h")
    return st.add_chunks(sid, pid, [Chunk(chunk_hash(url, t), url, "T", "", t) for t in texts])


@pytest.fixture
def mini_store():
    st = Store()
    st.add_snapshot(S1, "2026-05-12")
    st.add_snapshot(S2, "2026-05-19")
    st.add_snapshot(S5, "2026-09-28")
    shared = ["Auto loan APR as low as 5.39% for 60 months.", "Share savings dividends paid monthly."]
    _add(st, S1, "https://www.dupaco.com/auto", shared + ["Jumbo certificate special 4.10% APY."])
    _add(st, S2, "https://www.dupaco.com/auto", shared)
    _add(st, S5, "https://www.dupaco.com/auto", shared[:1] + ["Auto loan APR as low as 5.14% for 60 months."])
    _add(st, S5, "https://www.dupaco.com/cd", ["Certificate rates: 12-month certificate 3.90% APY."])
    return st


def _retriever(st, tmp_path, llm=None):
    r = Retriever(st, llm or FakeLLM(None), tmp_path / "index")
    for sid in (S1, S2, S5):
        r.index_snapshot(sid)
    return r


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def json(self, model, system, user, schema, **kw):
        self.calls.append(dict(model=model, system=system, user=user, schema=schema))
        return self.reply


def _hit(cid, score, url="u", text="t"):
    return Hit(cid, url, "", text, score, S5)


# fts

def test_match_expr_safe(mini_store):
    expr = to_match_expr('what\'s the "APR"?')
    tokens = expr.split(" OR ")
    assert all(t.startswith('"') and t.endswith('"') and '"' not in t[1:-1] for t in tokens)
    assert '"apr"' in tokens and '"rate"' in tokens  # APR <-> rate synonym
    assert '"what"' not in tokens and '"the"' not in tokens
    nasty = 'NOT (cd* AND "jumbo"): ^rate - OR near/5 {x} [y]'
    for q in ('what\'s the "APR"?', nasty):
        mini_store.fts_match(to_match_expr(q), S1, 5)  # no sqlite syntax error
    assert '"certificate"' in to_match_expr("best CD").split(" OR ")
    assert '"cd"' in to_match_expr("certificate").split(" OR ")
    assert to_match_expr("what is the?") == ""
    assert fts_search(mini_store, "what is the?", S1, 5) == []


def test_fts_search_order_and_hits(mini_store):
    hits = fts_search(mini_store, "60 month auto loan", S5, 10)
    assert hits and all(isinstance(h, Hit) and h.snapshot_id == S5 for h in hits)
    keys = [(-h.score, h.url, h.chunk_id) for h in hits]
    assert keys == sorted(keys)
    assert "5.14%" in hits[0].text or "5.39%" in hits[0].text


def test_snapshot_filter(mini_store, tmp_path, embedder):
    only1 = mini_store.new_chunks(S1)[2].chunk_id  # jumbo certificate, in 01 only
    assert "Jumbo" in mini_store.chunk(only1).chunk.text
    r = _retriever(mini_store, tmp_path)
    assert only1 in [h.chunk_id for h in r.search("jumbo certificate special", S1)]
    for sid in (S2, S5):
        assert only1 not in [h.chunk_id for h in r.search("jumbo certificate special", sid)]
        assert only1 not in [c for c, _ in r.dense.query("jumbo certificate special", sid, 10)]
    assert {h.snapshot_id for h in r.search("certificate", S5)} == {S5}


def test_fallback_bm25(mini_store, tmp_path, embedder, monkeypatch):
    monkeypatch.setattr(mini_store, "fts_available", False)
    hits = fts_search(mini_store, "jumbo certificate", S1, 5)
    assert hits and "Jumbo" in hits[0].text and hits[0].score > 0
    assert fts_search(mini_store, "jumbo certificate", S5, 5)[0].url.endswith("/cd")  # filtered to 05
    bm = Bm25Fallback(mini_store, S5)
    assert all(cid in mini_store.chunk_ids_in(S5) for cid, _ in bm.search("auto loan certificate", 10))
    r = _retriever(mini_store, tmp_path)
    assert r.search("jumbo certificate", S1)[0].text.startswith("Jumbo")


def test_index_embeds_each_chunk_once(mini_store, tmp_path, embedder):
    r = Retriever(mini_store, FakeLLM(None), tmp_path / "index")
    r.index_snapshot(S1)
    assert embedder.calls == 3
    r.index_snapshot(S2)  # same chunks recur: metadata update only
    assert embedder.calls == 3
    r.index_snapshot(S5)
    assert embedder.calls == 5
    shared = mini_store.chunk_ids_in(S1) & mini_store.chunk_ids_in(S5)
    meta = r.dense.collection.get(ids=[str(c) for c in sorted(shared)])["metadatas"]
    assert meta == [{"url": "https://www.dupaco.com/auto", "in_01": True, "in_02": True, "in_05": True}]


def test_init_wipes_stale_index(mini_store, tmp_path, embedder):
    r = _retriever(mini_store, tmp_path)
    assert r.dense.collection.count() == 5
    stale = tmp_path / "index" / "stale.bin"
    stale.write_bytes(b"x")
    r2 = Retriever(mini_store, FakeLLM(None), tmp_path / "index")
    assert not stale.exists() and r2.dense.collection.count() == 0
    r2.index_snapshot(S1)
    assert r2.dense.collection.count() == 3


def test_search_deterministic(mini_store, tmp_path, embedder):
    r = _retriever(mini_store, tmp_path)
    a = r.search("auto loan APR 60 months", S5)
    b = r.search("auto loan APR 60 months", S5)
    assert a == b and a
    keys = [(-h.score, h.url, h.chunk_id) for h in a]
    assert keys == sorted(keys)
    assert len(r.search("auto loan certificate savings", S1, k=2)) == 2
    assert r.search("the of and", S5) == []


def test_rrf_scores(mini_store, tmp_path, embedder):
    r = _retriever(mini_store, tmp_path)
    hits = r.search("Jumbo certificate special 4.10% APY", S1)
    assert hits[0].text.startswith("Jumbo") and math.isclose(hits[0].score, 1.0)  # first in both lists
    assert all(h.score < 1.0 for h in hits[1:])


def test_dense_failure_falls_back_to_fts(mini_store, tmp_path, embedder, monkeypatch):
    r = _retriever(mini_store, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("chroma down")
    monkeypatch.setattr(r.dense, "query", boom)
    hits = r.search("auto loan APR", S5)
    assert [h.chunk_id for h in hits] == [h.chunk_id for h in fts_search(mini_store, "auto loan APR", S5, 10)]


def test_index_failure_disables_dense(mini_store, tmp_path, embedder, monkeypatch):
    r = Retriever(mini_store, FakeLLM(None), tmp_path / "index")
    monkeypatch.setattr(r.dense, "index_snapshot", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    r.index_snapshot(S1)  # never raises
    assert r.search("jumbo certificate", S1)[0].text.startswith("Jumbo")


def test_fts_only_when_dense_absent_or_incomplete(mini_store, tmp_path, embedder, monkeypatch):
    fts_ids = [h.chunk_id for h in fts_search(mini_store, "auto loan APR", S5, 10)]
    partial = Retriever(mini_store, FakeLLM(None), tmp_path / "a")
    partial.index_snapshot(S1)  # 05 never indexed: dense finds nothing there
    got = partial.search("auto loan APR", S5)
    assert [h.chunk_id for h in got] == fts_ids
    assert math.isclose(got[0].score, 1.0)  # single-list RRF: the FTS top still counts as the RRF top
    assert [h.chunk_id for h in partial.pick("q", got)] == [got[0].chunk_id]  # LLM None fallback

    def no_chroma(*a, **k):
        raise ImportError("chromadb missing")
    monkeypatch.setattr("kb.retrieval.pipeline.DenseIndex", no_chroma)
    absent = Retriever(mini_store, FakeLLM(None), tmp_path / "b")
    absent.index_snapshot(S5)
    assert not absent.dense_ok
    got = absent.search("auto loan APR", S5)
    assert [h.chunk_id for h in got] == fts_ids and math.isclose(got[0].score, 1.0)
    assert all(a.score > b.score for a, b in zip(got, got[1:]))


class RandomEmbedder(HashEmbedder):
    """Pseudo-random unit vectors seeded by the text: no exact-distance ties, so ranks are well defined."""

    def __call__(self, input):
        import random
        out = []
        for t in input:
            rnd = random.Random(hashlib.sha256(t.encode()).digest())
            v = [rnd.gauss(0, 1) for _ in range(32)]
            n = math.sqrt(sum(x * x for x in v))
            out.append([x / n for x in v])
        return out


def test_dense_index_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setattr(dense, "embedding_function", RandomEmbedder)
    st = Store()
    st.add_snapshot(S5, "2026-09-28")
    words = ["auto", "loan", "rate", "certificate", "savings", "mortgage", "branch", "hours", "fee", "card",
             "checking", "member", "jumbo", "term", "month", "visa", "home", "equity", "student", "boat"]
    for u in range(100):  # 3,000 chunks over 100 pages: past Chroma's brute-force buffer, into HNSW
        _add(st, S5, f"https://www.dupaco.com/p{u}",
             [" ".join(words[(u * 7 + i * j) % 20] for j in range(1, 6)) + f" {u}-{i}" for i in range(30)])
    assert len(st.chunk_ids_in(S5)) == 3000
    runs = []
    for d in ("a", "b"):
        r = Retriever(st, FakeLLM(None), tmp_path / d)
        r.index_snapshot(S5)
        runs.append([[c for c, _ in r.dense.query(" ".join(words[i:i + 3]), S5, 25)] for i in range(18)])
    assert runs[0] == runs[1] and all(len(q) == 25 for q in runs[0])
    emb = RandomEmbedder()  # exact: equals a brute-force ranking by (-cosine, chunk_id)
    vecs = {c: emb([st.chunk(c).chunk.text])[0] for c in st.chunk_ids_in(S5)}
    for i in (0, 9):
        q = emb([" ".join(words[i:i + 3])])[0]
        brute = sorted(vecs, key=lambda c: (-sum(a * b for a, b in zip(q, vecs[c])), c))[:25]
        assert runs[0][i] == brute


def test_dense_matrix_cache_invalidated(mini_store, tmp_path, embedder):
    r = Retriever(mini_store, FakeLLM(None), tmp_path / "index")
    r.index_snapshot(S1)
    assert r.dense.query("certificate rates", S5, 10) == []  # 05 not indexed yet (cached as empty)
    r.index_snapshot(S5)
    got = [c for c, _ in r.dense.query("certificate rates", S5, 10)]
    assert sorted(got) == sorted(mini_store.chunk_ids_in(S5))


# picker

def test_picker_none():
    hits = [_hit(1, 1.0), _hit(2, 0.5)]
    llm = FakeLLM({"chunk_ids": [1], "none_relevant": True})
    assert pick_sections(llm, "construction loan rate?", hits) == []
    r = Retriever.__new__(Retriever)
    r.llm = llm
    assert r.pick("construction loan rate?", hits) == []


def test_picker_maps_ids(tmp_path):
    hits = [_hit(1, 1.0, text="a" * 2000), _hit(2, 0.5), _hit(3, 0.4)]
    llm = FakeLLM({"chunk_ids": [3, 99, 1, 2], "none_relevant": False})
    r = Retriever.__new__(Retriever)
    r.llm = llm
    assert [h.chunk_id for h in r.pick("q", hits)] == [3, 1]
    call = llm.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "a" * 600 in call["user"] and "a" * 601 not in call["user"]
    assert set(call["schema"]["properties"]) == {"chunk_ids", "none_relevant"}
    assert r.pick("q", []) == []
    assert len(llm.calls) == 1  # no call for no hits


def test_picker_llm_none_fallback():
    r = Retriever.__new__(Retriever)
    r.llm = FakeLLM(None)
    assert [h.chunk_id for h in r.pick("q", [_hit(7, 1.0), _hit(8, 0.5)])] == [7]
    assert r.pick("q", [_hit(7, 0.98), _hit(8, 0.98)]) == []
    r.llm = FakeLLM({"unexpected": 1})
    assert [h.chunk_id for h in r.pick("q", [_hit(7, 1.0)])] == [7]


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_picker_live(tmp_path):
    llm = LLM(tmp_path / "llm.sqlite", enabled=True)
    hits = [_hit(11, 1.0, "https://www.dupaco.com/auto",
                 "New and used auto loans. 60-month auto loan APR as low as 5.39%."),
            _hit(12, 0.9, "https://www.dupaco.com/cd", "12-month certificate 3.90% APY."),
            _hit(13, 0.8, "https://www.dupaco.com/home", "Construction loans: talk to a mortgage lender.")]
    got = pick_sections(llm, "What is the 60-month auto loan APR?", hits)
    assert got == [11], (got, dict(llm.failures))
    assert pick_sections(llm, "What is the construction loan interest rate?", hits) == []
    assert not llm.failures


# real data

def _real_store(path):
    """Pages + chunks of all 5 real snapshots via the current chunker (no extraction)."""
    st = Store(str(path))
    for s in real_snapshots():
        st.add_snapshot(s.snapshot_id, s.taken_on)
        for p in sorted(s.pages, key=lambda p: p.url):
            pid = st.add_page(s.snapshot_id, p.url, p.title, INFO, "h")
            st.add_chunks(s.snapshot_id, pid, normalize.chunk_page(p.url, p.title, p.markdown))
    return st


@pytest.fixture(scope="module")
def real_index(tmp_path_factory):
    d = tmp_path_factory.mktemp("real")
    st = _real_store(d / "kb.sqlite")
    r = Retriever(st, LLM(d / "llm.sqlite", enabled=False), d / "index")
    t0 = time.time()
    for s, _ in ALL5:
        t = time.time()
        r.index_snapshot(s)
        print(f"\nindex_snapshot {s}: {len(st.new_chunks(s))} new chunks, {time.time() - t:.1f}s", flush=True)
    return r, time.time() - t0


@pytest.mark.real
@pytest.mark.skipif(os.environ.get("KB_RUN_INDEX_TIME") != "1", reason="~30 min on CPU; set KB_RUN_INDEX_TIME=1")
def test_real_index_time(real_index):
    r, secs = real_index
    n = r.dense.collection.count()
    print(f"\nindexed {n} distinct chunks in {secs:.1f}s")
    assert r.dense_ok and n == r.store._db.execute("select count(*) from chunk").fetchone()[0]
    assert secs < 600


@pytest.mark.real
@pytest.mark.skipif(os.environ.get("KB_RUN_RECALL") != "1", reason="~7 min (builds the real dense index); set KB_RUN_RECALL=1")
def test_real_recall(request):
    probe = ROOT / "dev" / "retrieval_probe.jsonl"
    if not probe.exists():  # checked before building the (slow) real index
        pytest.skip("dev/retrieval_probe.jsonl not present (Track J)")
    from scoring import contains
    r, _ = request.getfixturevalue("real_index")
    reg = SnapshotRegistry()
    for sid, d in ALL5:
        reg.add(sid, d)
    items = [json.loads(l) for l in probe.read_text().splitlines() if l.strip()]
    ok = 0
    for it in items:
        sid = reg.resolve(effective_as_of(it["question"], it.get("as_of")))
        blob = "\n".join(h.text for h in r.search(it["question"], sid, k=10))
        ok += all(contains(blob, v) for v in it["must_include"])
    print(f"\nrecall {ok}/{len(items)}")
    assert ok >= 22
