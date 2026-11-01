import pytest
from contract import Answer
from scoring import REFUSAL_RE, _numbers, contains

from kb import compose
from kb.compose import refusal, render_facts, render_prose, sanitize
from kb.model import FactObs, Hit, Intent, ResolvedFact

SID = "05_2026-09-28_latest"
KEY = "auto_loan|apr|model_year=2024-2027;segment=personal;term=60"


def obs(value, key=KEY, url="https://www.dupaco.com/rates", page_type="rate_sheet", quote="q"):
    p, a, qs = key.split("|", 2)
    return FactObs(key, p, a, {}, value, value, SID, "2026-09-28", url, page_type, 100, None, quote, "table")


def rf(value, key=KEY, losers=(), url="https://www.dupaco.com/rates"):
    return ResolvedFact(key, value, obs(value, key, url), [obs(v, key, "https://www.dupaco.com/old", "blog") for v in losers],
                        "authority")


def intent(family="fact"):
    return Intent(family, "auto_loan", "apr", {}, None, "q", "q")


def test_template_matches_scorer():
    a = render_facts("q", intent(), [rf("5.39%")], SID)
    assert a.text == "The 60-month APR for 2024-2027 model-year vehicles is as low as 5.39% APR."
    assert contains(a.text, "5.39%") and contains(a.text, "5.39% APR")
    assert a.sources[0].url == "https://www.dupaco.com/rates" and a.sources[0].snapshot_id == SID
    assert a.sources[0].quote == "q" and a.conflict_note is None


def test_conflict_note_not_in_text():
    a = render_facts("q", intent(), [rf("5.39%", losers=["5.34%"])], SID)
    assert "5.34" not in a.text
    assert a.conflict_note == "Other pages show 5.34%; used rate_sheet page (authority)."


def test_list_one_line_per_fact_and_dedup_sources():
    k2 = "auto_loan|apr|model_year=2024-2027;segment=personal;term=72"
    a = render_facts("q", intent("list"), [rf("5.39%"), rf("5.49%", k2)], SID)
    assert len(a.text.split("\n")) == 2 and "72-month" in a.text and "5.49%" in a.text
    assert len(a.sources) == 1


def test_other_attributes_render_value():
    k = "branch|phone|name=Dubuque"
    a = render_facts("q", intent(), [rf("(563) 557-7600", k)], SID)
    assert "(563) 557-7600" in a.text and "Dubuque" in a.text
    k = "checking|min_balance|"
    assert "$25" in render_facts("q", intent(), [rf("$25", k)], SID).text


def test_refusal_has_no_digits():
    a = refusal("What is the 7/1 ARM rate?")
    assert not a.confident and REFUSAL_RE.search(a.text) and not _numbers(a.text)
    assert "isn't listed on the website" in a.text and "please contact dupaco" in a.text.lower()


def test_sanitize_drops_stale():
    out = sanitize(Answer("Now 5.39%. Was 5.34%."), {"5.39%"}, {"5.34%"})
    assert "5.34" not in out.text and "5.39%" in out.text


def test_sanitize_keeps_sentence_with_keep_value_and_drops_phrases():
    out = sanitize(Answer("It is 5.39% not 5.34%. Per context it holds. Would you like more? Fine."), {"5.39%"}, {"5.34%"})
    assert "5.34" in out.text and "context" not in out.text and "would you like" not in out.text.lower()
    assert "Fine." in out.text


def test_sanitize_unit_aware():
    out = sanitize(Answer("Costs $25 monthly. Rate 25% apply."), set(), {"$25"})
    assert "$25" not in out.text and "25%" in out.text


def test_sanitize_all_dropped_becomes_refusal():
    out = sanitize(Answer("Was 5.34%."), {"5.39%"}, {"5.34%"})
    assert not out.confident and not _numbers(out.text)


class NoLLM:
    def text(self, *a, **k):
        return None


class FakeLLM:
    def __init__(self, out):
        self.out, self.calls = out, []

    def text(self, model, system, user, **k):
        self.calls.append((system, user))
        return self.out


HIT = Hit(1, "https://www.dupaco.com/x", "A", "Dupaco offers free checking with no monthly fee. Our mascot is a bear. "
          "Overdraft transfers cost nothing.", 1.0, SID)
PI = Intent("prose", None, None, {}, None, "Does Dupaco charge a monthly fee for checking?", "k")


def test_prose_fallback_without_llm():
    a = render_prose(PI.cleaned_question, PI, [HIT], SID, NoLLM())
    assert "monthly fee" in a.text and "mascot" not in a.text and len(a.text) <= 400
    assert a.sources[0].url == HIT.url and a.sources[0].snapshot_id == SID


def test_prose_uses_llm_and_prompt_rules():
    llm = FakeLLM("Checking has no monthly fee.")
    a = render_prose(PI.cleaned_question, PI, [HIT], SID, llm)
    assert a.text == "Checking has no monthly fee." and a.sources[0].url == HIT.url
    assert "3 sentences" in llm.calls[0][0] and "question" in llm.calls[0][0].lower()


def test_no_question_back():
    for llm in (NoLLM(), FakeLLM("Which product would you like? It has no fee.")):
        assert "?" not in render_prose(PI.cleaned_question, PI, [HIT], SID, llm).text


def test_prose_llm_leaking_phrase_falls_back():
    a = render_prose(PI.cleaned_question, PI, [HIT], SID, FakeLLM("In these facts, checking is free."))
    assert "in these facts" not in a.text.lower()


def test_extractive_skips_faq_question_sentences():
    h = Hit(2, "https://www.dupaco.com/faq", "FAQ", "How do I open a checking account? Visit any branch with ID.", 1.0, SID)
    a = render_prose("How do I open a checking account?", PI, [h], SID, NoLLM())
    assert "?" not in a.text and "Visit any branch" in a.text


def test_extractive_only_questions_refuses():
    h = Hit(3, "https://www.dupaco.com/faq", "FAQ", "How do I open a checking account?", 1.0, SID)
    a = render_prose("How do I open a checking account?", PI, [h], SID, NoLLM())
    assert not a.confident and not _numbers(a.text)


def test_sanitize_does_not_split_abbreviations():
    t = "Lobby hours are 8:30 a.m. - 5:00 p.m. Monday-Friday."
    assert sanitize(Answer(t), {"8:30 a.m.", "5:00 p.m."}, {"9:00"}).text == t
    t = "Visit 123 Main St. Dubuque for help."
    assert sanitize(Answer(t), set(), {"$9"}).text == t


@pytest.mark.parametrize("lead", ["It was", "Thus it was", "Monitor rates were"])
def test_sanitize_ampm_sentence_end_still_drops_stale(lead):
    t = f"The 60-month rate is 5.39% at the branch open until 5 p.m. {lead} 5.34% before."
    out = sanitize(Answer(t), {"5.39%"}, {"5.34%"}).text
    assert "5.34" not in out and "5.39%" in out


_WEEKDAYS = ["Mon", "Monday", "Tue", "Tues", "Tuesday", "Wed", "Wednesday", "Thu", "Thur", "Thurs", "Thursday",
             "Fri", "Friday", "Sat", "Saturday", "Sun", "Sunday"]
_AMPM_CONTINUE = ["- 5 p.m.", "\u2013 5 p.m.", "7 days a week", "to 5 p.m."] + _WEEKDAYS + [
    w.lower() for w in _WEEKDAYS] + [w.upper() for w in _WEEKDAYS] + ["Mon-Fri", "Sat,", "Thurs, Fri", "Sun;"]
_AMPM_BOUNDARY = ["It", "Thus", "Monitor", "Money", "Friends", "Saturated", "Sunshine", "Wedge"]


@pytest.mark.parametrize("follow", _AMPM_CONTINUE)
def test_sentences_ampm_continues(follow):
    assert len(compose._sentences(f"Open 8:30 a.m. {follow} and 9 a.m. later.")) == 1


@pytest.mark.parametrize("follow", _AMPM_CONTINUE)
def test_sanitize_ampm_continues_keeps_whole_sentence(follow):
    # Merged: the keep value 8:30 rescues the sentence that also holds the stale 9. Split: the 9 part is dropped.
    t = f"Open 8:30 a.m. {follow} and 9 a.m. later."
    assert sanitize(Answer(t), {"8:30 a.m."}, {"9 a.m."}).text == t


@pytest.mark.parametrize("word", _AMPM_BOUNDARY)
def test_sentences_ampm_boundary(word):
    t = f"The 60-month rate is 5.39% at the branch open until 5 p.m. {word} it was 5.34% before."
    assert len(compose._sentences(t)) == 2


@pytest.mark.parametrize("word", _AMPM_BOUNDARY)
def test_sanitize_ampm_boundary_drops_stale(word):
    t = f"The 60-month rate is 5.39% at the branch open until 5 p.m. {word} it was 5.34% before."
    out = sanitize(Answer(t), {"5.39%"}, {"5.34%"}).text
    assert "5.34" not in out and "5.39%" in out


@pytest.mark.parametrize("t", ["Lobby hours are 8:30 a.m. - 5:00 p.m. Monday-Friday.",
                               "Open 8:30 a.m. Saturday 9 a.m. Monday-Friday.",
                               "Open 8:30 a.m. Wednesday 9 a.m. Mon-Fri.",
                               "Open 8:30 a.m. Mon-Fri and 9 a.m. Saturday.",
                               "Visit 123 Main St. Dubuque for help."])
def test_sentences_one_sentence(t):
    assert compose._sentences(t) == [t]


def test_sanitize_saturday_hours_intact():
    t = "Open 8:30 a.m. Saturday 9 a.m. Monday-Friday."
    assert sanitize(Answer(t), {"8:30 a.m."}, {"9 a.m."}).text == t


def test_sentences_splits_after_no():
    assert compose._sentences("Is there a fee? No. The fee is $5.") == ["Is there a fee?", "No.", "The fee is $5."]


def test_sanitize_splits_after_no():
    out = sanitize(Answer("Is there a fee? No. The fee is $5."), set(), {"$5"}).text
    assert "$5" not in out and "No." in out


# ---- Stage 2 final fix wave

def test_generic_rate_renders_apy_then_rate_in_one_line():
    """dup-050: a generic "rate" question shows APY (scored) first and the dividend rate with it."""
    k1, k2 = "bright_savings|apy|segment=personal;tier=0.01-1000000", "bright_savings|rate|segment=personal;tier=0.01-1000000"
    it = Intent("fact", "bright_savings", None, {}, None, "q", "q")
    a = render_facts("q", it, [rf("2.25%", k1), rf("2.23%", k2)], SID)
    assert a.text == ("The Bright Savings account APY for balances of $0.01-$1,000,000 is up to 2.25% APY, "
                      "with a dividend rate of 2.23%.")
    k3, k4 = "auto_loan|apr|segment=personal;term=60", "auto_loan|rate|segment=personal;term=60"
    a = render_facts("q", Intent("fact", "auto_loan", None, {}, None, "q", "q"), [rf("5.39%", k3), rf("5.20%", k4)], SID)
    assert a.text == "The 60-month APR for an auto loan is as low as 5.39% APR, with an interest rate of 5.20%."


def test_rate_range_and_names_render():
    k = "credit_card|apr|name=platinum-visa;segment=personal"
    a = render_facts("q", intent(), [rf("12.50% - 19.50%", k)], SID)
    assert a.text == "The Platinum Visa credit card APR is 12.50% - 19.50% APR."
    assert contains(a.text, "12.5") and contains(a.text, "Platinum Visa")
    k = "credit_card|apr|name=rewards-visa;segment=business"
    assert contains(render_facts("q", intent(), [rf("20.50%", k)], SID).text, "Business Rewards")
    k = "credit_card|apr|name=introductory-balance-transfer-rate;segment=personal"
    assert "Introductory Balance Transfer Rate credit card" in render_facts("q", intent(), [rf("2.90%", k)], SID).text


def test_band_text_is_human():
    k = "share_savings|apy|segment=personal;tier=25000-inf"
    assert "for balances of $25,000 and up" in render_facts("q", intent(), [rf("0.20%", k)], SID).text
    k = "share_savings|rate|segment=personal;tier=10000-24999.99"
    assert "$10,000-$24,999.99" in render_facts("q", intent(), [rf("0.15%", k)], SID).text
    k = "mortgage_fixed|rate|loan_amount=0-100000;segment=personal;term=360"
    assert "loan amounts up to $100,000" in render_facts("q", intent(), [rf("7.13%", k)], SID).text


def test_hours_template_polish():
    k = "branch|hours_lobby|day=mon-fri;name=hy-vee"
    a = render_facts("q", intent(), [rf("8:30 a.m. - 5:30 p.m.", k)], SID)
    assert a.text == "The Hy-Vee branch lobby hours are 8:30 a.m. - 5:30 p.m. Monday-Friday."
    k = "branch|hours_driveup|day=sat;name=madison-mineral-point-road"
    a = render_facts("q", intent(), [rf("8 a.m. - 12 p.m.", k)], SID)
    assert a.text == "The Madison Mineral Point Road branch drive-up hours are 8 a.m. - 12 p.m. Saturday."
    assert ".." not in a.text


def test_prose_none_is_refusal():
    a = render_prose(PI.cleaned_question, PI, [HIT], SID, FakeLLM("NONE"))
    assert not a.confident and REFUSAL_RE.search(a.text)
    # LLM failure (None) still answers extractively
    assert "monthly fee" in render_prose(PI.cleaned_question, PI, [HIT], SID, NoLLM()).text


def test_extractive_strips_chunk_title_prefix():
    text = ("Auto & Motorcycle Loans and Sales | Dupaco > Auto Loans For Every Ride > Financing, Right There\n"
            "Galena Chrysler 937 Galena Square Dr, Galena, IL, 61036")
    h = Hit(4, "https://www.dupaco.com/auto", "Auto Loans For Every Ride > Financing, Right There", text, 1.0, SID)
    a = render_prose("Where can I finance a car?", PI, [h], SID, NoLLM())
    assert "| Dupaco" not in a.text and "Galena Chrysler" in a.text
    h = Hit(5, "https://www.dupaco.com/x", "", "Checking | Dupaco\nFree checking has no monthly fee.", 1.0, SID)
    assert render_prose("monthly fee?", PI, [h], SID, NoLLM()).text == "Free checking has no monthly fee."


def test_prose_sections_show_page_url_and_link_targets():
    """A "what link / how do I join online" answer needs the page URL and the targets of links the section shows."""
    llm = FakeLLM("Join online at https://join.dupaco.com/.")
    h = Hit(5, "https://www.dupaco.com/about/join", "Join", "Ready to join?\nBecome a member\nContinue later", 1.0, SID)
    links = {h.url: [("Become a member", "https://join.dupaco.com/"), ("Unrelated footer", "https://x.example/")]}
    a = render_prose("How do I join online?", PI, [h], SID, llm, links=links)
    system, user = llm.calls[0]
    assert "https://www.dupaco.com/about/join" in user
    assert "Become a member: https://join.dupaco.com/" in user and "x.example" not in user
    assert "link" in system.lower()
    assert a.text == "Join online at https://join.dupaco.com/."
    render_prose("How do I join online?", PI, [h], SID, llm)  # links are optional
    assert "join.dupaco.com" not in llm.calls[1][1]


@pytest.mark.parametrize("out", [
    "The provided information doesn't state a specific number of branches. It only says Dupaco has 600+ employees. NONE",
    "The sections don't give a specific interest rate for a construction loan. It is a 1/1 ARM that can adjust 2%.",
    "NONE.",
])
def test_prose_disclaimer_or_trailing_none_is_refusal(out):
    """The model sometimes explains what the sections lack (and answers a neighbouring question) instead of NONE."""
    a = render_prose("How many branches?", PI, [HIT], SID, FakeLLM(out))
    assert not a.confident and not _numbers(a.text)


def test_prose_meta_lead_in_is_not_a_disclaimer():
    out = "Based on the sections provided, the locator is at https://www.dupaco.com/contact. It lists every branch."
    a = render_prose("Branch locator URL?", PI, [HIT], SID, FakeLLM(out))
    assert a.confident and "dupaco.com/contact" in a.text
