import json
from pathlib import Path

import pytest

from kb.intent import parse


class FakeLLM:
    def __init__(self, out):
        self.out, self.calls = out, 0

    def json(self, model, system, user, schema, max_tokens=2048, timeout_s=8.0):
        self.calls += 1
        return self.out


def q(i):
    return i.product, i.attribute, i.qualifiers


def test_auto():
    i = parse("What's the APR for a 60-month auto loan on a 2025 model-year vehicle?")
    assert (i.product, i.attribute, i.qualifiers.get("term"), i.qualifiers.get("model_year")) == \
        ("auto_loan", "apr", "60", "2025")
    assert i.qualifiers["segment"] == "personal" and i.family == "fact"


def test_auto_2024_vehicle():
    assert parse("What's the APR for an 84-month auto loan on a 2024 vehicle?").qualifiers["model_year"] == "2024"


def test_paraphrases_same_canonical():
    a = parse("What is the APY on Dupaco's 17-month special certificate?")
    b = parse("17 month CD special — what's the yield?")
    assert a.canonical == b.canonical
    assert (a.product, a.attribute, a.qualifiers["term"]) == ("certificate_special", "apy", "17")


def test_jumbo_special():
    i = parse("What was the rate on the 10-month fixed jumbo special certificate as of May 2026?")
    assert q(i)[0] == "certificate_special" and i.qualifiers["kind"] == "jumbo" and i.qualifiers["term"] == "10"


def test_chitchat_stripped():
    i = parse("For my coffee table book: what's the routing number?")
    assert "coffee table" not in i.cleaned_question
    assert (i.product, i.attribute) == ("org", "routing_number")
    assert "coffee" not in parse("Hi there! I'm not asking again. What is Dupaco's routing number?").cleaned_question


def test_highest():
    i = parse("What's the highest share savings rate?")
    assert i.superlative == "highest" and i.product == "share_savings"
    assert parse("What is the highest Share Savings APY?").attribute == "apy"


def test_list_family():
    i = parse("Which credit cards do you offer and their APRs?")
    assert (i.family, i.product, i.attribute) == ("list", "credit_card", "apr")
    assert parse("I would like to know the rates for the credit cards").family == "list"


def test_unknown_is_prose():
    i = parse("Tell me about your community impact")
    assert i.family == "prose" and i.product is None and i.attribute is None


def test_term_years_to_months():
    i = parse("Can you tell me the interest rate for a 30-year mortgage?")
    assert (i.product, i.attribute, i.qualifiers["term"]) == ("mortgage_fixed", None, "360")  # generic rate
    assert parse("15 year mortgage APR").qualifiers["term"] == "180"


@pytest.mark.parametrize("text,amt", [
    ("Share savings dividend rate with a balance between $10,000 and $24,999?", "10000"),
    ("Share savings rate for exactly $25,000", "25000"),
    ("Share savings rate for $24,999.99", "24999.99"),
])
def test_tier_amounts(text, amt):
    i = parse(text)
    assert i.product == "share_savings" and i.qualifiers["tier"] == amt and "loan_amount" not in i.qualifiers


def test_loan_amount_jumbo():
    i = parse("What is the rate on a $100,000 jumbo mortgage?")
    assert i.product == "mortgage_fixed" and i.qualifiers["loan_amount"] == "100000"
    assert "tier" not in i.qualifiers


def test_segment():
    assert parse("business auto loan APR").qualifiers["segment"] == "business"
    assert parse("auto loan APR").qualifiers["segment"] == "personal"
    assert "segment" not in parse("What is the address of the Galena Branch?").qualifiers


def test_branch_facts():
    i = parse("What is the address of the Galena Branch?")
    assert (i.product, i.attribute, i.qualifiers) == ("branch", "address", {"name": "galena"})
    assert parse("What are the drive-up hours?").attribute == "hours_driveup"
    assert parse("What are your lobby hours?").attribute == "hours_lobby"
    i = parse("How many branches does Dupaco have?")
    assert (i.family, i.product, i.attribute, i.qualifiers) == ("fact", "org", "count", {"what": "branches"})
    i = parse("When does the Hy-Vee branch close?")
    assert (i.attribute, i.qualifiers["name"]) == ("closure_date", "hy-vee")
    assert parse("Is the Hy-Vee branch still open today (October 2026)?").attribute == "status"


def test_no_attribute_products_still_fact():
    for text, p in [("What interest rate do you charge on a construction loan?", "construction_loan"),
                    ("What is your rate on a 7/1 adjustable-rate mortgage?", "mortgage_arm")]:
        i = parse(text)
        assert (i.family, i.product, i.attribute) == ("fact", p, None)  # generic rate: resolve widens to APR


def test_canonical_is_valid_key_and_stable():
    a = parse("What is the APR on a 36-month personal loan?")
    assert a.canonical == parse("what is the apr on a 36-month personal loan").canonical
    assert a.canonical.startswith("personal_loan|apr|segment=personal;term=36|fact|")
    assert parse("highest share savings apy").canonical != parse("share savings apy").canonical


def test_llm_fallback_closed_labels():
    fake = FakeLLM({"product": "student_loan", "attribute": "eligibility"})
    i = parse("I am headed to a new university, any flexible lending?", fake)
    assert (i.family, i.product, i.attribute) == ("fact", "student_loan", "eligibility") and fake.calls == 1


def test_llm_not_called_when_rules_hit_and_bad_output_ignored():
    fake = FakeLLM({"product": "student_loan", "attribute": "eligibility"})
    parse("What is the APR on a 36-month personal loan?", fake)
    assert fake.calls == 0
    for bad in (None, {"product": "none", "attribute": "none"}, {"product": "bogus", "attribute": "apr"}, "x"):
        assert parse("Tell me about your community impact", FakeLLM(bad)).family == "prose"


def test_llm_raising_is_soft():
    class Boom:
        def json(self, *a, **k):
            raise RuntimeError("x")
    assert parse("Tell me about your community impact", Boom()).family == "prose"


def test_odd_input_does_not_raise():
    for s in ("", "   ", "$$$ ;=| ???", "a;b=c|d rate"):
        parse(s)


@pytest.mark.skipif(not Path("eval/public_eval.jsonl").exists(), reason="no eval file")
def test_public_eval_never_raises_and_canonical_valid():
    for line in Path("eval/public_eval.jsonl").read_text().splitlines():
        i = parse(json.loads(line)["question"])
        assert i.canonical and i.family in ("fact", "list", "prose")


def test_award_and_eligibility():
    i = parse("What recognition has Dupaco received from Forbes?")
    assert (i.product, i.attribute, i.qualifiers) == ("org", "award", {"name": "forbes"})
    assert parse("Who is eligible to become a Dupaco member?").attribute == "eligibility"


@pytest.mark.parametrize("q,name", [
    ("What is the APR on the Dupaco Platinum Elite Visa card?", "platinum-elite-visa"),
    ("What APR does the Dupaco Galaxy Rewards Mastercard charge?", "galaxy-rewards-mastercard"),
    ("What APY does the Dupaco Sapphire Money Market account pay?", "sapphire"),
    ("What is the APY on Moonshot Teen Checking?", "moonshot-teen"),
    ("What dividend rate does Dupaco's gold IRA pay?", "gold"),
    ("What APY does Titanium Business Checking earn?", "titanium"),
    ("What APY does Launchpad Savings pay?", "launchpad"),
    ("What APY does the Achieve Money Market pay?", "achieve"),
    ("What is the APR on the Business Platinum Visa?", "platinum-visa"),
    ("What APR does the Visa Platinum card have?", "platinum-visa"),
    ("What is the highest Share Savings APY?", None),
    ("What is the APY on 1st Rate Checking?", None),
    ("What are business checking rates?", None),
    ("What is the APY on Dupaco's regular 12-month certificate?", None),
])
def test_named_account_qualifier(q, name):
    """Words naming a specific account before its noun become `name`, so a fabricated account never matches a real
    one's facts (refusal fixture); descriptive words (business, highest, regular, 1st Rate) do not."""
    assert parse(q).qualifiers.get("name") == name


def test_paraphrase_pairs_parse_alike():
    """Paraphrase-fixture groups that split (dup-087, dup-102, dup-008, dup-049, dup-060)."""
    a, b = parse("What recognition has Dupaco received from Forbes?"), parse("How many times has Forbes recognized Dupaco?")
    assert a.qualifiers == b.qualifiers == {"name": "forbes"}
    i = parse("What are the lobby hours at Dupaco's Mullan Avenue location?")
    assert (i.attribute, i.qualifiers) == ("hours_lobby", {"name": "mullan-avenue"})
    assert parse("Where on Dupaco's website can I look up certificate (CD) rates?").canonical == \
        parse("What link should I use to see Dupaco's CD / certificate rates?").canonical
    assert parse("Does any Share Savings balance tier earn 0.35% APY?").family == "list"
    assert parse("How do I change the address or phone number Dupaco has on file for me?").family == "prose"


def test_names_are_hyphen_slugs():
    assert parse("When does the Hy-Vee branch close?").qualifiers["name"] == "hy-vee"
    assert parse("lobby hours for the Mullan Avenue branch").qualifiers["name"] == "mullan-avenue"
    assert parse("standard APR on the Dupaco Platinum Visa").qualifiers["name"] == "platinum-visa"


def test_lowercase_branch_name_and_no_name():
    i = parse("what is the lobby hours at the galena branch?")
    assert (i.product, i.attribute, i.qualifiers) == ("branch", "hours_lobby", {"name": "galena"})
    i = parse("What are the lobby hours at any branch?")
    assert i.qualifiers == {}
    assert parse("What are your lobby hours?").qualifiers == {}


def test_at_least_not_superlative():
    i = parse("auto loan APR for at least $5,000")
    assert i.superlative is None
    assert parse("What is the lowest auto loan APR?").superlative == "lowest"


def test_word_boundaries():
    assert parse("I am interested in your corporate culture").family == "prose"
    assert parse("Do you operate on Sundays?").attribute is None


# ---- Stage 2 final fix wave

@pytest.mark.parametrize("text,product", [
    ("What is the rate for the Bright Savings account?", "bright_savings"),
    ("Provide current checking account interest rates", "checking"),
    ("I would like to know the rates for the credit cards", "credit_card"),
    ("auto loan rate", "auto_loan"),
])
def test_generic_rate_is_fact_with_no_attribute(text, product):
    i = parse(text)
    assert (i.product, i.attribute) == (product, None) and i.family in ("fact", "list")
    assert i.canonical.endswith("|generic")


def test_explicit_rate_attributes_kept():
    assert parse("What is the APY on Bright Savings?").attribute == "apy"
    assert parse("credit card APR").attribute == "apr"
    i = parse("What dividend rate do I earn on Share Savings with a balance between $10,000 and $24,999?")
    assert (i.product, i.attribute) == ("share_savings", "rate")
    assert parse("Bright Savings rate").canonical != parse("Bright Savings dividend rate").canonical


def test_generic_rate_without_rate_product_is_prose():
    assert parse("What is the interest rate at the Galena branch?").family == "prose"


@pytest.mark.parametrize("text", ["How many branches does Dupaco have?", "how many locations do you have",
                                  "What is the number of branch offices?"])
def test_branch_count_matches_c2_key(text):
    i = parse(text)
    assert (i.product, i.attribute, i.qualifiers) == ("org", "count", {"what": "branches"})


def test_tiers_question_is_list():
    assert parse("Do any Dupaco savings tiers pay 0.35% APY?").family == "list"


def test_branch_question_slug_matches_extractor_slug_and_resolves():
    """R43: a question naming a street branch selects its address; a city-only question lists every branch there."""
    from kb.extract.regex import _addr_slug
    from kb.keys import parse_key
    from kb.model import AUTHORITY, FactObs, ResolvedFact
    from kb.resolve import select_for_intent

    def rf(street, city):
        key = f"branch|address|name={_addr_slug(street, city)}"
        o = FactObs(key, "branch", "address", parse_key(key)[2], street, street, "s5", "2026-09-28", "https://x/contact",
                    "branch", AUTHORITY["branch"], None, "", "regex")
        return ResolvedFact(key, street, o, [], "single source")

    res = [rf("3299 Hillcrest Rd.", "Dubuque"), rf("1465 Sycamore St.", "Dubuque"),
           rf("5865 Saratoga Road", "Asbury"), rf("11375 Oldenburg Ln.", "Galena")]
    i = parse("What is the Asbury Road branch address?")
    assert i.qualifiers == {"name": "asbury-road"}
    assert [r.value for r in select_for_intent(i, res)] == ["5865 Saratoga Road"]
    assert [r.value for r in select_for_intent(parse("What is the address of the Hillcrest Rd. branch?"), res)] \
        == ["3299 Hillcrest Rd."]
    i = parse("What is the address of the Dubuque branch?")
    assert i.qualifiers == {"name": "dubuque"}
    assert sorted(r.value for r in select_for_intent(i, res)) == ["1465 Sycamore St.", "3299 Hillcrest Rd."]
    i = parse("What is the address of the Dubuque branch on Asbury?")
    assert i.qualifiers == {"name": "dubuque"}
