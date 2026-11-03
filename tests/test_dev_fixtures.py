"""Track J: structural checks on the dev eval fixtures (no snapshot data needed except the
`real` checks, which confirm that probe values literally appear in the cited snapshot)."""
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from scoring import contains, score_conflicts

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "dev"
PUBLIC = ROOT / "eval" / "public_eval.jsonl"
EVAL_KEYS = {"id", "question", "as_of", "expected_answer", "must_include", "must_not_include",
             "category", "difficulty", "weight", "expected_sources", "group"}
FIXTURES = ["paraphrase_eval.jsonl", "asof_eval.jsonl", "refusal_eval.jsonl",
            "completeness_temporal_eval.jsonl"]
SNAP_IDS = ["01_2026-05-12_baseline", "02_2026-05-19_recrawl", "03_2026-06-09_recrawl",
            "04_2026-08-27_rebaseline", "05_2026-09-28_latest"]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _public() -> list[dict]:
    return _jsonl(PUBLIC)


@pytest.mark.parametrize("name", FIXTURES)
def test_fixtures_valid(name):
    items = _jsonl(DEV / name)
    assert items, name
    public_q = {it["question"].strip().lower() for it in _public()}
    for it in items:
        assert set(it) == EVAL_KEYS, (name, it.get("id"), set(it) ^ EVAL_KEYS)
        assert it["question"].strip().lower() not in public_q, it["id"]
        assert isinstance(it["must_include"], list) and isinstance(it["must_not_include"], list)
        if it["category"] == "should_refuse":
            assert it["must_include"] == []
        else:
            assert it["must_include"], it["id"]
        # an expected value must never also be a trap in the same item
        for v in it["must_include"]:
            assert not any(contains(v, t) for t in it["must_not_include"]), (it["id"], v)


def test_ids_unique_across_dev_and_public():
    ids = [it["id"] for it in _public()]
    for name in FIXTURES:
        ids += [it["id"] for it in _jsonl(DEV / name)]
    assert len(ids) == len(set(ids))


def test_paraphrase_one_per_public_item_same_group():
    pub = _public()
    para = _jsonl(DEV / "paraphrase_eval.jsonl")
    assert len(para) == len(pub)
    by_group = {it["group"] or it["id"]: it for it in pub}
    for p in para:
        orig = by_group[p["group"]]
        assert p["question"] != orig["question"]
        assert p["category"] == orig["category"]
        assert p["as_of"] == orig["as_of"]


def _asof_fact(it: dict) -> str:  # ids look like asof-<fact>-<YYYY-MM-DD>
    return it["id"].split("-")[1]


def test_asof_shape():
    items = _jsonl(DEV / "asof_eval.jsonl")
    # no paraphrase group: answers legitimately differ across dates
    assert all(it["group"] is None and it["category"] == "temporal_as_of" for it in items)
    facts = {_asof_fact(it) for it in items}
    assert len(facts) == 6
    for f in facts:
        dates = sorted(it["as_of"] for it in items if _asof_fact(it) == f)
        assert len(dates) == 8, f  # 5 snapshot dates + between + before + after
        assert dates[0] < "2026-05-12" and dates[-1] > "2026-09-28"
        assert {"2026-05-12", "2026-05-19", "2026-06-09", "2026-08-27", "2026-09-28"} <= set(dates)


def test_refusal_shape():
    items = _jsonl(DEV / "refusal_eval.jsonl")
    refuse = [it for it in items if it["category"] == "should_refuse"]
    answer = [it for it in items if it["category"] != "should_refuse"]
    assert len(refuse) == 13 and len(answer) == 12  # 10 look-alikes + solar/wedding (real answers)


def _ct() -> dict[str, dict]:
    return {it["id"]: it for it in _jsonl(DEV / "completeness_temporal_eval.jsonl")}


def test_completeness_temporal_annual_vto():
    """Annual VTO cases: explicit years map to their own count, the other year is a trap, and
    unavailable years are refusals (empty must_include), not zero-token answers."""
    ct = _ct()
    assert ct["ct-vto-2023"]["must_include"] == ["294"] and "256" in ct["ct-vto-2023"]["must_not_include"]
    assert ct["ct-vto-2024"]["must_include"] == ["256"] and "294" in ct["ct-vto-2024"]["must_not_include"]
    assert ct["ct-vto-latest"]["must_include"] == ["256"] and "294" in ct["ct-vto-latest"]["must_not_include"]
    for i in ("ct-vto-2025", "ct-vto-last-year"):
        assert ct[i]["category"] == "should_refuse" and ct[i]["must_include"] == []
        assert ct[i]["must_not_include"] == []


def test_completeness_temporal_certificates_distinguish_tiers():
    ct = _ct()
    inv = ct["ct-cert-inventory"]
    # inventory must carry several distinct terms, not one coincidental number
    assert len(inv["must_include"]) >= 5 and inv["category"] == "list_completeness"
    assert any("Which certificate term" in t for t in inv["must_not_include"])
    tier = ct["ct-cert-17-tiers"]
    # special and jumbo rates AND their minimums are separately required
    assert {"4.10%", "4.20%", "$1,000", "$100,000"} <= set(tier["must_include"])
    jumbo = ct["ct-cert-10-jumbo-current"]
    assert jumbo["must_include"] == ["4.00%"] and jumbo["must_not_include"] == ["3.90%"]


def test_completeness_temporal_no_wrong_year_in_expected():
    for it in _ct().values():
        if it["category"] == "should_refuse":
            continue
        assert it["weight"] > 0 and it["expected_answer"]


def test_conflicts_gold_valid():
    gold = json.loads((DEV / "conflicts_gold.json").read_text())
    assert 20 <= len(gold) <= 30
    assert len({g["id"] for g in gold}) == len(gold)
    for g in gold:
        assert set(g) == {"id", "topic_keywords", "values", "correct_value"}, g["id"]
        assert len(g["values"]) >= 2 and g["topic_keywords"], g["id"]
        assert g["correct_value"] in g["values"], g["id"]


def _perfect_submission(gold):
    # one Conflict-like object per gold entry: its keywords as topic, its values, the right winner
    return [SimpleNamespace(topic=" / ".join(g["topic_keywords"]), values=list(g["values"]),
                            resolution=g["correct_value"]) for g in gold]


def test_conflicts_gold_order_independent():
    """scoring.score_conflicts is greedy (first match wins), so every gold entry must match only
    its own conflict: a perfect submission must score 1.0 in any order."""
    gold = json.loads((DEV / "conflicts_gold.json").read_text())
    subs = _perfect_submission(gold)
    orders = [subs, subs[::-1]]
    for seed in range(5):
        shuffled = subs[:]
        random.Random(seed).shuffle(shuffled)
        orders.append(shuffled)
    for order in orders:
        rep = score_conflicts(gold, order)
        assert rep["f1"] == 1.0 and rep["resolution_accuracy"] == 1.0, rep


def test_retrieval_probe_shape():
    probes = _jsonl(DEV / "retrieval_probe.jsonl")
    assert len(probes) == 36
    for p in probes:
        assert set(p) == {"question", "snapshot_id", "must_include"}
        assert p["snapshot_id"] in SNAP_IDS and p["must_include"]


@pytest.mark.real
def test_retrieval_probe_values_in_snapshot():
    from conftest import real_snapshots
    snaps = {s.snapshot_id: s for s in real_snapshots()}
    counts = []
    for p in _jsonl(DEV / "retrieval_probe.jsonl"):
        n = sum(all(v in pg.markdown for v in p["must_include"]) for pg in snaps[p["snapshot_id"]].pages)
        counts.append((n, p["snapshot_id"][:2], p["question"]))
        assert n >= 1, p["question"]
    for n, sid, q in sorted(counts, reverse=True)[:8]:
        print(f"probe pages-with-all-values={n:3d}  {sid}  {q}")
    # values must be specific enough that only a handful of pages hold them all
    assert max(n for n, _, _ in counts) <= 12, sorted(counts, reverse=True)[:3]
