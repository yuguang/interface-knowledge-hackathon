import time

import pytest
from contract import Page

from conftest import real_snapshots
from kb.model import chunk_hash
from kb.normalize import chunk_page, clean, fit_boilerplate


def _pages(n, common_on, extra=None):
    """n pages; the line 'Shared footer' sits on the first `common_on` of them."""
    pages = []
    for i in range(n):
        lines = [f"Unique body {i}"]
        if i < common_on:
            lines.append("Shared footer")
        if extra and i == 0:
            lines.append(extra)
        pages.append(Page(f"https://x/{i}", f"T{i}", "\n\n".join(lines), "2026-05-12T00:00:00Z"))
    return pages


def test_boilerplate_threshold():
    bp = fit_boilerplate(_pages(10, 3, extra="Rare line"))
    assert "Shared footer" in bp          # 3/10 >= 20%
    assert "Rare line" not in bp          # 1/10
    assert "Unique body 0" not in bp
    assert "Shared footer" not in fit_boilerplate(_pages(10, 1))
    assert clean("Unique body 0\n\nShared footer", bp) == "Unique body 0"


def test_boilerplate_ignores_structure_and_counts_pages_once():
    pages = [Page(f"u{i}", "t", "| a | b |\n| --- | --- |\n| 1 | 2 |\n\nRepeat\nRepeat\nRepeat", "") for i in range(5)]
    pages += [Page(f"v{i}", "t", f"only {i}", "") for i in range(95)]
    bp = fit_boilerplate(pages)           # 5/100 pages: below threshold even though lines repeat
    assert bp == frozenset()
    assert "| --- | --- |" not in fit_boilerplate(pages[:5])


def test_clean_strips_images_and_links():
    assert clean("![x](a.png) see [rates](https://x/y)", frozenset()) == "see rates"
    assert clean('[go](https://x/y "title") and [a](https://x/(b))', frozenset()) == "go and a"
    assert clean("[powered by\\\n![logo](l.png)](https://array.com)\n\nkeep", frozenset()).endswith("keep")


def test_clean_drops_recaptcha_and_vin_rows():
    md = ("| Year | Make | VIN | Price |\n| --- | --- | --- | --- |\n"
          "| 2018 | Alfa | ZASFAKPN0J7B61419 | $8,550.00 |\n| 2020 | Kia | KNAGT4L3XG5078184 | $9,100 |\n\n"
          "protected by **reCAPTCHA**\n\nRecaptcha requires verification.\n\nReal text")
    out = clean(md, frozenset())
    assert out == "Real text"             # orphaned header and separator go too
    keep = clean("| Term | Rate |\n| --- | --- |\n| 60 | 5.39% |", frozenset())
    assert "5.39%" in keep and "| Term | Rate |" in keep


def test_table_never_split():
    rows = "\n".join(f"| row{i} | value number {i} |" for i in range(200))
    md = f"## Rates\n\n| Term | Rate |\n| --- | --- |\n{rows}"
    chunks = chunk_page("https://x/p", "Auto Loans", md)
    tchunks = [c for c in chunks if "| row" in c.text]
    assert len(tchunks) > 1
    for c in tchunks:
        body = c.text.split("\n", 1)[1]
        assert body.startswith("| Term | Rate |\n| --- | --- |\n| row")
        assert len(c.text) <= 1500 + len("Auto Loans > Rates") + 1
    seen = [ln for c in tchunks for ln in c.text.splitlines() if ln.startswith("| row")]
    assert seen == [f"| row{i} | value number {i} |" for i in range(200)]   # no row lost, split or repeated


def test_small_table_is_one_chunk_with_its_own_text():
    md = "Intro text.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nAfter."
    chunks = chunk_page("u", "T", md)
    t = [c for c in chunks if "| 1 | 2 |" in c.text]
    assert len(t) == 1 and t[0].text.split("\n", 1)[1].startswith("| a | b |")


def test_heading_path_prefix():
    md = "# Auto Loans\n\nWelcome.\n\n## Rates\n\nRates are low.\n\n### New\n\nNew cars.\n\n## Terms\n\nTerm text."
    chunks = chunk_page("https://x/a", "Auto Loans", md)
    by = {c.heading_path: c for c in chunks}
    assert by["Rates"].text.startswith("Auto Loans > Rates\n")
    assert "Rates are low." in by["Rates"].text
    assert by["Rates > New"].text.startswith("Auto Loans > Rates > New\n")
    assert by["Terms"].text.startswith("Auto Loans > Terms\n")   # sibling pops the deeper heading
    assert by[""].text.startswith("Auto Loans\n") and "Welcome." in by[""].text
    for c in chunks:
        assert c.hash == chunk_hash("https://x/a", c.text)
        assert c.url == "https://x/a" and c.title == "Auto Loans"


def test_chunk_max_len():
    para = " ".join(f"word{i}." for i in range(3000))   # one huge paragraph, no blank lines
    md = "## Big\n\n" + para + "\n\n" + "\n\n".join(f"Paragraph {i} " + "x" * 300 for i in range(20))
    chunks = chunk_page("u", "Big Page", md)
    prefix = "Big Page > Big"
    assert len(chunks) > 5
    assert all(len(c.text) <= 1500 + len(prefix) for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert "word2999." in joined and "Paragraph 19" in joined


def test_empty_and_duplicates():
    assert chunk_page("u", "T", "") == []
    assert chunk_page("u", "T", "## Only heading") == []
    chunks = chunk_page("u", "T", "## A\n\nsame\n\n## A\n\nsame")
    assert len({c.hash for c in chunks}) == len(chunks)


@pytest.mark.real
def test_real_dedup_ratio():
    t0 = time.time()
    hashes, total = set(), 0
    for snap in real_snapshots():
        bp = fit_boilerplate(snap.pages)
        for p in snap.pages:
            for c in chunk_page(p.url, p.title, clean(p.markdown, bp)):
                hashes.add(c.hash)
                total += 1
    wall = time.time() - t0
    print(f"\nDISTINCT_HASHES={len(hashes)} TOTAL_CHUNKS={total} WALL={wall:.1f}s")
    assert 0 < len(hashes) <= 30_000


def test_page_links_keep_absolute_targets_with_link_text():
    from kb.normalize import page_links
    md = ('[Become a member](https://join.dupaco.com/)\n![logo](https://x/l.png)\n'
          '[Rates](/rates "t") [call](tel:800) [mail](mailto:a@b) [Rates](https://x/r) [Join](https://join.dupaco.com/)\n'
          '[**Apply** now](https://apply.example.com/a?b=(1))')
    assert page_links(md) == [("Become a member", "https://join.dupaco.com/"), ("Rates", "https://x/r"),
                              ("Join", "https://join.dupaco.com/"), ("Apply now", "https://apply.example.com/a?b=(1)")]
    assert page_links("no links here") == []
