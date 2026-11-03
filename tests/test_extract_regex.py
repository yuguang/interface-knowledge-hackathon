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
    assert d == {("hours_lobby", "mon-fri"): "9 a.m. - 5 p.m.", ("hours_lobby", "sat"): "Closed",
                 ("hours_lobby", "sun"): "Closed", ("hours_driveup", "mon-fri"): "8 a.m. - 6 p.m."}


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
    assert get(obs, "branch|address|name=dubuque-main-st") == ["123 Main St, Dubuque, IA 52001"]
    assert get(obs, "branch|phone|name=dubuque-main-st") == ["563-555-1234"]


def test_po_box_not_address_org_phone():
    obs = regex.extract(ctx("P.O. Box 179, Dubuque, IA 52004\n\n**Phone:** 800-373-7600"))
    assert not [o for o in obs if o.attribute == "address"]
    assert get(obs, "org|phone|") == ["800-373-7600"]


def test_award_and_program():
    md = ("June 18, 2024\n\nForbes again named Dupaco one of the top credit unions. This was the fifth time Forbes recognized Dupaco.\n\n"
          "Dupaco partners on the Home$tart program. This initiative provides up to $15,000 in assistance.")
    obs = regex.extract(ctx(md))
    assert get(obs, "org|award|name=forbes;year=2024") == ["fifth"]
    assert get(obs, "program|detail|name=homestart") == ["$15,000"]


def test_quote_bounded_and_no_chunks():
    obs = regex.extract(ctx("Dupaco routing number: 273974581 " + "x " * 300, chunk=False))
    assert obs and len(obs[0].quote) <= 200 and obs[0].chunk_id is None


def test_hours_and_address_share_branch_name():
    md = "Lobby\nMonday-Friday: 9 a.m.-5 p.m.\n\n**Address:** 123 Main St, Dubuque, IA 52001"
    obs = regex.extract(ctx(md))
    assert {o.qualifiers["name"] for o in obs} == {"dubuque-main-st"}


def test_award_keyed_by_year_and_news_counts_kept():
    md = "June 18, 2024\n\nThis was the fifth time Forbes recognized Dupaco.\nDupaco has 22 branches."
    obs = regex.extract(ctx(md, url="https://www.dupaco.com/newsroom/forbes"))
    assert get(obs, "org|award|name=forbes;year=2024") == ["fifth"]
    # 89bd310: news/blog boilerplate counts are kept (the resolver's newest-date rule picks the current one)
    assert get(obs, "org|count|what=branches") == ["22"]
    obs = regex.extract(ctx("Dupaco has 22 branches.", url="https://www.dupaco.com/about"))
    assert get(obs, "org|count|what=branches") == ["22"]


@pytest.mark.real
def test_real_routing_counts_awards_all_snapshots():
    from kb import classify
    snaps = real_snapshots()
    assert len(snaps) == 5
    for snap in snaps:
        obs = []
        for p in snap.pages:
            info = classify.classify_page(p.url, p.title, p.markdown, snap.taken_on)
            c = PageContext(snap.snapshot_id, snap.taken_on, p.url, p.title, info, p.markdown, [])
            obs += regex.extract(c)
        by = {}
        for o in obs:
            by.setdefault(o.fact_key, set()).add(o.value_norm)
        assert by["org|routing_number|"] == {"273974581"}, snap.snapshot_id
        # branch counts come from news/blog boilerplate (89bd310); the resolver's newest-date rule picks one
        assert by["org|count|what=branches"], snap.snapshot_id
        assert all(int(v) >= 10 for v in by["org|count|what=branches"]), snap.snapshot_id
        awards = {k: v for k, v in by.items() if k.startswith("org|award|")}
        assert awards == {"org|award|name=forbes;year=2023": {"fourth"},
                          "org|award|name=forbes;year=2024": {"fifth"}}, snap.snapshot_id


# ---- Stage 2 final fix wave

@pytest.mark.parametrize("raw,norm", [("8 a.m.–6 p.m.", "8 a.m. - 6 p.m."), ("8:30 a.m. - 5:30 p.m.", "8:30 a.m. - 5:30 p.m."),
                                      ("9am-5pm", "9 a.m. - 5 p.m."), ("8:00 a.m. - 12 p.m.", "8:00 a.m. - 12 p.m.")])
def test_hours_keep_site_time_form(raw, norm):
    """dup-066 gold "8 a.m." must survive: whole hours are not rewritten to "8:00 a.m."."""
    obs = regex.extract(ctx(f"Drive-up\nMonday-Friday: {raw}"))
    assert [o.value_norm for o in obs if o.attribute == "hours_driveup"] == [norm]


def test_address_comma_before_zip_and_context():
    md = ("### Dupaco Credit Union\n\nDupaco Branch\n\n11375 Oldenburg Ln., Galena, IL, 61036\n\nClosed\n\n"
          "### Dupaco ATM @ Fareway Food Store\n\nSurcharge-Free ATM\n\n2050 JFK Rd., Dubuque, IA, 52001\n\n"
          "### Galena Chrysler\n\n937 Galena Square Dr, Galena, IL, 61036")
    obs = regex.extract(ctx(md, url="https://www.dupaco.com/contact"))
    assert {o.fact_key: o.value_norm for o in obs if o.attribute == "address"} == {
        "branch|address|name=galena-oldenburg-ln": "11375 Oldenburg Ln., Galena, IL 61036"}


@pytest.mark.real
def test_real_galena_address_snapshot_05():
    from kb import classify
    snap = [s for s in real_snapshots() if s.snapshot_id.startswith("05")][0]
    vals = set()
    for p in snap.pages:
        info = classify.classify_page(p.url, p.title, p.markdown, snap.taken_on)
        for o in regex.extract(PageContext(snap.snapshot_id, snap.taken_on, p.url, p.title, info, p.markdown, [])):
            if o.fact_key == "branch|address|name=galena-oldenburg-ln":
                vals.add(o.value_norm)
    assert vals == {"11375 Oldenburg Ln., Galena, IL 61036"}


# ---- Task 3.2a

def test_four_dubuque_addresses_get_four_keys():
    """R43: branches in one city must not share a fact key (a shared key = wrong answer + false conflict)."""
    md = "\n\n".join(f"### Dupaco Credit Union\n\nDupaco Branch\n\n{a}, Dubuque, IA, {z}\n\nClosed"
                    for a, z in [("3299 Hillcrest Rd.", "52001"), ("3999 Pennsylvania Ave.", "52002"),
                                 ("1465 Sycamore St.", "52001"), ("2245 Flint Hill Dr.", "52003")])
    obs = regex.extract(ctx(md, url="https://www.dupaco.com/contact"))
    got = {o.fact_key: o.value_norm for o in obs if o.attribute == "address"}
    assert got == {
        "branch|address|name=dubuque-hillcrest-rd": "3299 Hillcrest Rd., Dubuque, IA 52001",
        "branch|address|name=dubuque-pennsylvania-ave": "3999 Pennsylvania Ave., Dubuque, IA 52002",
        "branch|address|name=dubuque-sycamore-st": "1465 Sycamore St., Dubuque, IA 52001",
        "branch|address|name=dubuque-flint-hill-dr": "2245 Flint Hill Dr., Dubuque, IA 52003"}


def test_phone_uses_same_slug_as_address():
    md = "**Address:** 123 Main St, Dubuque, IA 52001\n\n**Phone:** (563) 555-1234"
    obs = regex.extract(ctx(md))
    assert get(obs, "branch|address|name=dubuque-main-st") == ["123 Main St, Dubuque, IA 52001"]
    assert get(obs, "branch|phone|name=dubuque-main-st") == ["563-555-1234"]


VTO_2023 = "294 Dupaco employees used a new paid benefit in 2023 called Volunteer Time Off"
VTO_2024 = "256 Dupaco employees used a paid benefit in 2024 called Volunteer Time Off (or VTO)"


def test_vto_employee_count_is_keyed_by_reporting_year():
    md = f"Community impact\n\n{VTO_2023}, giving back to the community.\n\n{VTO_2024} to support causes."
    obs = regex.extract(ctx(md))
    assert get(obs, "org|count|what=vto-employees;year=2023") == ["294"]
    assert get(obs, "org|count|what=vto-employees;year=2024") == ["256"]
    o = [x for x in obs if x.fact_key.endswith("year=2023")][0]
    assert o.extractor == "regex" and o.chunk_id == 7
    assert "294 Dupaco employees" in o.quote and len(o.quote) <= 200


@pytest.mark.parametrize("text", [
    "Dupaco has 650 employees and offers Volunteer Time Off in 2024.",
    "Employees volunteered 3,400 hours through Volunteer Time Off in 2024.",
    "12 Dupaco employees served on boards in 2024.",
    "256 Dupaco employees used Volunteer Time Off last year.",
    "256 Dupaco members used a paid benefit in 2024 called Volunteer Time Off.",
    "256 Dupaco employees attended training in 2024.",
])
def test_vto_count_rejects_unrelated_numbers(text):
    assert not [o for o in regex.extract(ctx(text)) if "vto-employees" in o.fact_key]


def test_vto_multi_year_sentence_never_attaches_a_year():
    text = "294 employees used a benefit in 2022, up from 100 in 2021 called VTO"
    assert not [o for o in regex.extract(ctx(text)) if "vto-employees" in o.fact_key]


def test_vto_comma_count_and_leading_year_form():
    obs = regex.extract(ctx("1,294 Dupaco employees used a paid benefit in 2024 called Volunteer Time Off."))
    assert get(obs, "org|count|what=vto-employees;year=2024") == ["1294"]
    # leading-year form is deliberately unmatched (kept out to avoid wrong-year risk)
    obs = regex.extract(ctx("In 2023, 294 Dupaco employees used Volunteer Time Off."))
    assert not [o for o in obs if "vto-employees" in o.fact_key]
