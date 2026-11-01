import pytest

from kb.extract import regex
from kb.model import PageContext, PageInfo, Chunk, StoredChunk, chunk_hash

from conftest import real_snapshots


def ctx(md, url="https://www.dupaco.com/x", title="T", chunk=True):
    chunks = []
    if chunk:
        chunks = [StoredChunk(7, Chunk(chunk_hash(url, md), url, title, "", md))]
    return PageContext("05_2026-09-28_latest", "2026-09-28", url, title, PageInfo("branch", 70, None), md, chunks)


def get(obs, key):
    return [o.value_norm for o in obs if o.fact_key == key]


def test_routing_checksum():
    obs = regex.extract(ctx("Dupaco routing number: 273974581"))
    assert get(obs, "org|routing_number|") == ["273974581"]
    assert obs[0].extractor == "regex" and obs[0].chunk_id == 7


def test_bad_checksum_dropped():
    assert regex.extract(ctx("Dupaco routing number: 273974582")) == []


def test_other_institution_routing_not_org():
    obs = regex.extract(ctx("Home Savings Bank routing number 271176899"))
    assert not [o for o in obs if o.fact_key.startswith("org|routing_number")]


def test_hours_noon():
    obs = regex.extract(ctx("Saturday: 8:30 a.m. - noon"))
    o = [x for x in obs if x.attribute == "hours_lobby"]
    assert len(o) == 1 and o[0].qualifiers["day"] == "sat" and "name" in o[0].qualifiers
    assert o[0].value_norm == "8:30 a.m. - noon"


def test_hours_driveup_block():
    md = "**Lobby**\nMonday-Friday: 9 a.m.–5 p.m.\nSaturday-Sunday: Closed\n**Drive-up** Monday-Friday: 8 a.m.–6 p.m."
    obs = regex.extract(ctx(md))
    d = {(o.attribute, o.qualifiers["day"]): o.value_norm for o in obs}
    assert d == {("hours_lobby", "mon-fri"): "9:00 a.m. - 5:00 p.m.", ("hours_lobby", "sat"): "Closed",
                 ("hours_lobby", "sun"): "Closed", ("hours_driveup", "mon-fri"): "8:00 a.m. - 6:00 p.m."}


def test_prose_hours_not_extracted():
    assert regex.extract(ctx("We'll reply Monday–Friday, 8:30 a.m.–5:30 p.m. to your form.")) == []


def test_closure_date():
    obs = regex.extract(ctx("The Hy-Vee branch will close March 31, 2026."))
    assert get(obs, "branch|closure_date|name=hy-vee") == ["March 31, 2026"]


def test_closing_costs_not_closure():
    assert regex.extract(ctx("You'll likely pay closing costs. The branch will close soon.")) == []


def test_branch_count():
    obs = regex.extract(ctx("Dupaco has more than 650 employees and 22 branches."))
    assert get(obs, "org|count|what=branches") == ["22"]
    assert regex.extract(ctx("He works with leaders across Dupaco's 18 branches.")) == []


def test_address_and_phone():
    md = "**Address:** 123 Main St, Dubuque, IA 52001\n\n**Phone:** (563) 555-1234"
    obs = regex.extract(ctx(md))
    assert get(obs, "branch|address|name=dubuque") == ["123 Main St, Dubuque, IA 52001"]
    assert get(obs, "branch|phone|name=dubuque") == ["563-555-1234"]


def test_po_box_not_address_org_phone():
    obs = regex.extract(ctx("P.O. Box 179, Dubuque, IA 52004\n\n**Phone:** 800-373-7600"))
    assert not [o for o in obs if o.attribute == "address"]
    assert get(obs, "org|phone|") == ["800-373-7600"]


def test_award_and_program():
    md = ("Forbes again named Dupaco one of the top credit unions. This was the fifth time Forbes recognized Dupaco.\n\n"
          "Dupaco partners on the Home$tart program. This initiative provides up to $15,000 in assistance.")
    obs = regex.extract(ctx(md))
    assert get(obs, "org|award|name=forbes") == ["fifth"]
    assert get(obs, "program|detail|name=homestart") == ["$15,000"]


def test_quote_bounded_and_no_chunks():
    obs = regex.extract(ctx("Dupaco routing number: 273974581 " + "x " * 300, chunk=False))
    assert obs and len(obs[0].quote) <= 200 and obs[0].chunk_id is None


@pytest.mark.real
def test_real_routing_and_branch_count():
    snap = [s for s in real_snapshots() if s.snapshot_id.startswith("05")][0]
    obs = []
    for p in snap.pages:
        obs += regex.extract(ctx(p.markdown, p.url, p.title))
    assert {o.value_norm for o in obs if o.fact_key == "org|routing_number|"} == {"273974581"}
    counts = {o.value_norm for o in obs if o.fact_key == "org|count|what=branches"}
    print("branch counts:", counts)
    assert "22" in counts
