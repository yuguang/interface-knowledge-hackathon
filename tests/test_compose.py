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
