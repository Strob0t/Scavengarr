"""Tests for scripts/probes/title_match.py: ids, verdicts and the table."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult

_PROBE = Path(__file__).resolve().parents[3] / "scripts/probes/title_match.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("title_match", _PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves the module's annotations through sys.modules
    sys.modules["title_match"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()

_REF = TitleMatchInfo(
    title="Haus des Geldes",
    year=2017,
    alt_titles=["Money Heist"],
    content_type="series",
)


def _result(title: str) -> SearchResult:
    return SearchResult(title=title, download_link="https://hoster.test/1")


def test_parse_id_series_and_movie() -> None:
    req, category = probe.parse_id("series/tt5753856:1:2")
    assert (req.imdb_id, req.content_type, req.season, req.episode, category) == (
        "tt5753856",
        "series",
        1,
        2,
        5000,
    )
    req, category = probe.parse_id("movie/tt0133093")
    assert (req.imdb_id, req.content_type, req.season, category) == (
        "tt0133093",
        "movie",
        None,
        2000,
    )
    with pytest.raises(ValueError):
        probe.parse_id("tt0133093")


def test_describe_reference() -> None:
    assert (
        probe.describe(_REF) == "Haus des Geldes (2017; alt: Money Heist; kind unknown)"
    )
    assert probe.describe(TitleMatchInfo(title="Dark")) == "Dark (kind unknown)"
    anime = TitleMatchInfo(
        title="One Piece", year=1999, imdb_id="tt0388629", animation=True
    )
    assert probe.describe(anime) == "One Piece (1999; tt0388629; animation)"
    live = TitleMatchInfo(title="One Piece", imdb_id="tt11737520", animation=False)
    assert probe.describe(live) == "One Piece (tt11737520; not animation)"


def test_verdicts_score_against_the_threshold_best_first() -> None:
    hits = probe.verdicts(
        [_result("Something Else Entirely"), _result("Haus des Geldes")],
        _REF,
        0.7,
        {},
    )
    assert [h.title for h in hits] == ["Haus des Geldes", "Something Else Entirely"]
    assert hits[0].kept and hits[0].score >= 0.7
    assert not hits[1].kept and hits[1].score < 0.7


def test_a_shorter_title_is_a_partial_verdict() -> None:
    shippuden = TitleMatchInfo(
        title="Naruto: Shippūden", year=2007, content_type="series"
    )
    hits = probe.verdicts(
        [_result("Naruto"), _result("Naruto Shippuden")], shippuden, 0.7, {}
    )
    assert {(h.title, h.kept, h.reason) for h in hits} == {
        ("Naruto Shippuden", True, "score"),
        ("Naruto", True, "partial"),
    }


def test_partial_hits_count_only_without_another_kept_hit() -> None:
    hits = [
        probe.Hit("Naruto Shippuden", None, 1.0, True, "score"),
        probe.Hit("Naruto", None, 1.0, True, "partial"),
    ]
    probe.drop_partial(hits)
    assert [h.kept for h in hits] == [True, False]

    alone = [
        probe.Hit("Naruto", None, 1.0, True, "partial"),
        probe.Hit("Boruto", None, 0.364, False, "score"),
    ]
    probe.drop_partial(alone)
    assert [h.kept for h in alone] == [True, False]


def test_render_lists_plugins_queries_and_results() -> None:
    runs = [
        probe.PluginRun(
            plugin="a",
            languages=["de"],
            reference="Dark (2017)",
            queries={"Dark": 2},
            hits=[
                probe.Hit("Dark", None, 1.0, True, "score"),
                probe.Hit("Dark Matter", "Dark.Matter.S01E01", 0.4, False, "score"),
                probe.Hit("Dark (2025)", None, 0.0, False, "year"),
            ],
        ),
        probe.PluginRun(
            plugin="b",
            languages=["de"],
            reference="Dark (2017)",
            failures={"Dark": "timeout"},
        ),
        probe.PluginRun(
            plugin="c", languages=["en"], reference="Dark (2017)", queries={"Dark": 0}
        ),
    ]
    text = probe.render("series/tt5753856:1:1", runs, 0.7)
    assert "Reference: Dark (2017); threshold 0.7." in text
    assert "| a | de | 'Dark' (2) | 3 | 1 | – |" in text
    assert "| b | de | – | 0 | 0 | 'Dark': timeout |" in text
    assert "No results: c." in text
    assert "- a: 'Dark' 1.00 kept" in text
    assert "- a: 'Dark Matter' [Dark.Matter.S01E01] 0.40 dropped\n" in text
    assert "- a: 'Dark (2025)' 0.00 dropped by year" in text
