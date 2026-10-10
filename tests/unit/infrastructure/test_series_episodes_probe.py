"""Tests for scripts/probes/series_episodes.py: the result classification."""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.plugins.base import SearchResult

_PROBE = Path(__file__).resolve().parents[3] / "scripts/probes/series_episodes.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("series_episodes", _PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves the module's annotations through sys.modules
    sys.modules["series_episodes"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _result(
    title: str,
    labels: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> SearchResult:
    links = None
    if labels is not None:
        links = [
            {"link": f"https://hoster.test/{i}", "label": label}
            for i, label in enumerate(labels)
        ]
    return SearchResult(
        title=title,
        download_link="https://hoster.test/0",
        download_links=links,
        metadata=metadata or {},
    )


def test_series_ids_reads_series_lines_once() -> None:
    lines = [
        "# comment",
        "movie/tt1  # A film",
        "series/tt2:1:1  # A show",
        "series/tt2:1:1",
        "series/tt3:2:5",
    ]
    assert probe.series_ids(lines) == ["series/tt2:1:1", "series/tt3:2:5"]


def test_parse_id() -> None:
    req = probe.parse_id("series/tt0903747:1:2")
    assert (req.imdb_id, req.content_type, req.season, req.episode) == (
        "tt0903747",
        "series",
        1,
        2,
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("S01E05 Pilot", (1, 5)),
        ("1x05", (1, 5)),
        ("Folge 5", (None, 5)),
        ("Episode 12 - VOE", (None, 12)),
        ("E07", (None, 7)),
        ("VOE", (None, None)),
        ("Staffel 1", (None, None)),
    ],
)
def test_label_numbers(label: str, expected: tuple[int | None, int | None]) -> None:
    assert probe.label_numbers(label) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result("Dark.S01E01.German.1080p.WEB.x264"), "season_episode"),
        (_result("Dark S01 German 1080p"), "season"),
        (_result("Dark"), "none"),
        (_result("Dark", metadata={"episode": 1}), "episode"),
    ],
)
def test_numbering(result: SearchResult, expected: str) -> None:
    assert probe.numbering(result) == expected


def test_metadata_wins_over_release_name() -> None:
    result = _result("Dark.S01E02.German", metadata={"season": 1, "episode": 1})
    assert probe.result_numbers(result) == ({1}, {1})


def test_metadata_without_ints_falls_back_to_release_name() -> None:
    result = _result("Show - S01E01 - Pilot", metadata={"season": "1", "episode": 1})
    assert probe.result_numbers(result) == ({1}, {1})


def test_filter_outcome_kept_narrowed_dropped() -> None:
    right = _result("Dark.S01E01.German.1080p")
    wrong = _result("Dark.S01E02.German.1080p")
    page = _result("Dark", labels=["1x1", "1x2"])
    assert probe.filter_outcome(right, 1, 1) == ("kept", right)
    assert probe.filter_outcome(wrong, 1, 1) == ("dropped", None)
    outcome, kept = probe.filter_outcome(page, 1, 1)
    assert outcome == "narrowed"
    assert kept is not None and kept.download_links == [
        {"link": "https://hoster.test/0", "label": "1x1"}
    ]


def test_classify_unlabelled_page_is_dropped() -> None:
    """A page without an episode number and without labelled links would
    pass with every episode: the filter drops it (continue-cut-searches)."""
    rec = probe.classify("series/tt1:1:1", "p", _result("Dark", ["VOE"]), 1, 1)
    assert (rec.numbering, rec.outcome, rec.right, rec.leaks) == (
        "none",
        "dropped",
        False,
        [],
    )


def test_classify_a_title_named_episode_is_narrowed_by_its_labels() -> None:
    """The title names the episode and the links carry episode words: the
    filter keeps the episode's link only (the leak class of the step 21
    review, row 30); the record lists the page's labels."""
    page = _result("Dark.S01E01", ["Folge 1", "Folge 2"])
    rec = probe.classify("series/tt1:1:1", "p", page, 1, 1)
    assert (rec.outcome, rec.right, rec.leaks) == ("narrowed", True, [])
    assert (rec.links, rec.kept_links) == (2, 1)
    assert rec.labels == ["Folge 1", "Folge 2"]


def test_classify_a_season_page_with_episode_words_is_narrowed() -> None:
    """The filter reads ``Folge 2`` like ``1x2`` and ``S01E02``: a season
    page with such labels keeps the episode's link."""
    page = _result("Dark Staffel 1", ["Folge 1", "Folge 2"])
    rec = probe.classify("series/tt1:1:1", "p", page, 1, 1)
    assert (rec.outcome, rec.right, rec.leaks, rec.kept_links) == (
        "narrowed",
        True,
        [],
        1,
    )


def test_classify_right_episode_from_title() -> None:
    rec = probe.classify(
        "series/tt1:1:1", "p", _result("Dark.S01E01.German", ["VOE"]), 1, 1
    )
    assert (rec.outcome, rec.right, rec.leaks, rec.kept_links) == (
        "kept",
        True,
        [],
        1,
    )


def test_render_counts_per_plugin_and_lists_leaks() -> None:
    records = [
        probe.classify("series/tt1:1:1", "a", _result("Dark.S01E01"), 1, 1),
        probe.classify("series/tt1:1:1", "a", _result("Dark.S01E02"), 1, 1),
        # a leak as the oracle would report one (the filter lets none through)
        dataclasses.replace(
            probe.classify(
                "series/tt1:1:1",
                "b",
                _result("Dark.S01E01", ["Folge 1", "Folge 2"]),
                1,
                1,
            ),
            leaks=["label 'Folge 2'"],
        ),
    ]
    failures = {"c": probe.Counter(timeout=2)}
    text = probe.render(records, failures, ["a", "b", "c"])
    assert "| a | 2 | 2 | 0 | 0 | 0 | 1 | 0 | 1 | 1 | 0 | 0 | 0 |" in text
    assert "| b | 1 | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 1 | 0 | 0 |" in text
    assert "| c | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | timeout 2 |" in text
    assert "| **total** | 3 |" in text
    assert "- b, series/tt1:1:1: 'Dark.S01E01' (label 'Folge 2')" in text


# ---------------------------------------------------------------------------
# The episode reference and the placements of locating plugins
# ---------------------------------------------------------------------------


_LABOON_REF = EpisodeRef(5, 2, "Laboon", "2001-03-21", absolute=62)


def test_parse_id_of_a_kitsu_episode() -> None:
    req = probe.parse_id("series/kitsu:12:1089")
    assert (req.imdb_id, req.content_type, req.season, req.episode) == (
        "kitsu:12",
        "series",
        None,
        1089,
    )


async def test_request_translates_a_kitsu_id_first() -> None:
    one_piece = probe.parse_id("series/tt0388629:1:1")
    translate = AsyncMock(return_value=one_piece)
    state = SimpleNamespace(anime_ids=SimpleNamespace(translate=translate))

    assert await probe._request(state, "series/kitsu:12:1089") == (one_piece, 1089)
    assert translate.await_args.args == (probe.parse_id("series/kitsu:12:1089"),)

    laboon = probe.parse_id("series/tt0388629:5:2")
    assert await probe._request(state, "series/tt0388629:5:2") == (laboon, None)

    translate.return_value = None
    assert await probe._request(state, "series/kitsu:12:1089") is None


def test_reference_line_names_the_catalog_entry() -> None:
    req = probe.parse_id("series/tt0388629:5:2")
    assert probe._reference(req, None) == "S5E2 (no reference)"
    assert (
        probe._reference(req, _LABOON_REF)
        == "S5E2 absolute 62 title 'Laboon' aired 2001-03-21"
    )


def test_placement_names_the_located_page() -> None:
    located = _result(
        "One Piece - S05E02",
        metadata={"site_season": 2, "site_episode": 1, "episode_located_by": "number"},
    )
    assert probe.placement(located) == "staffel-2/episode-1 by number"
    assert probe.placement(_result("Dark.S01E01")) == ""


class _RecordingPlugin:
    """A plugin recording its search calls."""

    locates_episodes = False

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
        **kw: object,
    ) -> list[SearchResult]:
        self.calls.append({"query": query, "season": season, "episode": episode, **kw})
        return []


class _LocatingPlugin(_RecordingPlugin):
    locates_episodes = True


async def test_search_hands_the_reference_to_locating_plugins_only() -> None:
    req = probe.parse_id("series/tt0388629:5:2")
    plain, locating = _RecordingPlugin(), _LocatingPlugin()

    await probe._search(plain, "one piece", req, 5, _LABOON_REF)
    await probe._search(locating, "one piece", req, 5, _LABOON_REF)
    await probe._search(locating, "one piece", req, 5, None)

    assert plain.calls == [{"query": "one piece", "season": 5, "episode": 2}]
    assert locating.calls == [
        {"query": "one piece", "season": 5, "episode": 2, "episode_ref": _LABOON_REF},
        {"query": "one piece", "season": 5, "episode": 2},
    ]


def test_render_lists_the_references_and_placements() -> None:
    located = _result(
        "One Piece - S05E02 - Ein Bad in Magensäure",
        ["VOE"],
        metadata={
            "season": 5,
            "episode": 2,
            "site_season": 2,
            "site_episode": 1,
            "episode_located_by": "number",
        },
    )
    records = [
        probe.classify("series/tt0388629:5:2", "sto", located, 5, 2),
        probe.classify("series/tt0388629:5:2", "a", _result("One.Piece.S05E02"), 5, 2),
    ]
    reference = "S5E2 absolute 62 title 'Laboon' aired 2001-03-21"
    references = {"series/tt0388629:5:2": reference}

    text = probe.render(records, {}, ["a", "sto"], references)

    lines = text.splitlines()
    assert lines[0].endswith("| leaks | located | failed |")
    assert lines[2].startswith("| a | 1 |") and lines[2].endswith("| 0 | 0 |")
    assert lines[3].startswith("| sto | 1 |") and lines[3].endswith("| 1 | 0 |")
    assert lines[4].startswith("| **total** | 2 |") and lines[4].endswith("| 1 | 0 |")
    assert f"- series/tt0388629:5:2: {reference}" in text
    assert (
        "- sto, series/tt0388629:5:2: 'One Piece - S05E02 - Ein Bad in Magensäure'"
        " -> staffel-2/episode-1 by number"
    ) in text


def test_render_without_references_or_placements_lists_none() -> None:
    text = probe.render([], {}, ["a"])
    assert "References:" not in text and "Placements:" not in text
