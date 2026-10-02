"""Tests for scripts/stremio_measure.py (title set, no live requests)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "stremio_measure.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stremio_measure", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load()


def test_title_set_ids() -> None:
    """17 titles as in docs/plans/stremio-latency.md; runs 1-8 measured
    "Ali" (tt0248667) instead of Der Schuh des Manitu (tt0248408)."""
    titles = [t for group in _mod.GROUPS.values() for t in group]
    assert len(titles) == 17
    ids = {label: sid for _, sid, label in titles}
    assert ids["Der Schuh des Manitu"] == "tt0248408"
    for ctype, sid, _ in titles:
        pattern = r"tt\d+" if ctype == "movie" else r"tt\d+:\d+:\d+"
        assert re.fullmatch(pattern, sid), sid


def test_source_is_the_last_description_line() -> None:
    stream = {"description": "Dark - S01E01\nGerman Dub\nVOE · sto"}
    assert _mod._source(stream) == "VOE · sto"
    assert _mod._source({}) == "?"
