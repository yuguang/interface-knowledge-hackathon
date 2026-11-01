import pytest

from kb.model import FactObs, Intent, PageContext, PageInfo, ResolvedFact


def test_documented_unhashable_types_are_unhashable():
    """model.py comments say these frozen dataclasses are unhashable (dict/list fields); keep that true."""
    info = PageInfo("other", 40, None)
    obs = FactObs("org|count|", "org", "count", {}, "1", "1", "s", "2026-01-01", "u", "other", 40, None, "q", "regex")
    for v in (PageContext("s", "2026-01-01", "u", "t", info, "md", []), obs, ResolvedFact("k", "1", obs, [], "r"),
              Intent("prose", None, None, {}, None, "q", "q")):
        with pytest.raises(TypeError):
            hash(v)
    hash(info)  # PageInfo has only scalar fields
