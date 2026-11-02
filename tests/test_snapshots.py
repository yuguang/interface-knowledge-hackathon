import pytest

from kb.snapshots import effective_as_of, parse_question_date


@pytest.mark.parametrize("as_of,sid", [(None, "05"), ("2026-01-01", "01"), ("2026-05-12", "01"), ("2026-05-31", "02"),
                                       ("2026-08-26", "03"), ("2026-09-28", "05"), ("2027-03-01", "05")])
def test_resolve(registry_all5, as_of, sid):
    assert registry_all5.resolve(as_of).startswith(sid)


def test_ids_ordered_and_latest(registry_all5):
    assert registry_all5.ids()[0].startswith("01") and registry_all5.latest().startswith("05")
    assert registry_all5.taken_on("03_2026-06-09_recrawl") == "2026-06-09"


@pytest.mark.parametrize("q,d", [("rate as of May 2026?", "2026-05-31"), ("as of May 15, 2026", "2026-05-15"),
                                 ("in June 2026", "2026-06-30"), ("what was it as of 2026-06-01", "2026-06-01"),
                                 ("as it stood on June 9, 2026", "2026-06-09"), ("back in May 2026", "2026-05-31"),
                                 ("What APY did the jumbo special pay in May 2026?", "2026-05-31"),
                                 ("back last year", "None"), ("what is it now", "None")])
def test_question_date_phrases(q, d):
    assert str(parse_question_date(q)) == d


@pytest.mark.parametrize("q", ["Is the Hy-Vee branch closing March 31, 2026?",
                               "Do may 2026 graduates qualify?",
                               "What did the May 2026 press release say?",
                               "Is the branch closing in March 2026?",
                               "what was it on 2026-06-01"])
def test_question_date_unanchored_is_none(q):
    """Ruling R15: dates not anchored by as of / as it stood / back in / in <Month YYYY> are not an as_of."""
    assert parse_question_date(q) is None


def test_arg_beats_text():
    assert effective_as_of("as of May 2026", "2026-09-01") == "2026-09-01"
    assert effective_as_of("as of May 2026", None) == "2026-05-31"
