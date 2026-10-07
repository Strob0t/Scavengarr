"""Tests for scripts/probes/series_episodes.py: the result classification."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

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


def test_classify_unlabelled_page_is_kept_without_the_episode() -> None:
    rec = probe.classify("series/tt1:1:1", "p", _result("Dark", ["VOE"]), 1, 1)
    assert (rec.numbering, rec.outcome, rec.right, rec.leaks) == (
        "none",
        "kept",
        False,
        [],
    )


def test_classify_leak_from_labels_the_filter_does_not_read() -> None:
    page = _result("Dark Staffel 1", ["Folge 1", "Folge 2"])
    rec = probe.classify("series/tt1:1:1", "p", page, 1, 1)
    assert rec.outcome == "kept"
    assert rec.right
    assert rec.leaks == ["label 'Folge 2'"]
    assert rec.labels == ["Folge 1", "Folge 2"]


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
        probe.classify(
            "series/tt1:1:1", "b", _result("Dark", ["Folge 1", "Folge 2"]), 1, 1
        ),
    ]
    failures = {"c": probe.Counter(timeout=2)}
    text = probe.render(records, failures, ["a", "b", "c"])
    assert "| a | 2 | 2 | 0 | 0 | 0 | 1 | 0 | 1 | 1 | 0 | 0 |" in text
    assert "| b | 1 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 1 | 1 | 0 |" in text
    assert "| c | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | timeout 2 |" in text
    assert "| **total** | 3 |" in text
    assert "- b, series/tt1:1:1: 'Dark' (label 'Folge 2')" in text
