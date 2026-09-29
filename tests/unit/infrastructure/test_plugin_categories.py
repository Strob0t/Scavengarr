"""Tests for the shared Torznab category helpers of the plugins."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    STREAM_CATEGORIES,
    category_matches,
    filter_by_category,
    is_series_title,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase


class TestCategoryMatches:
    def test_no_request_matches_everything(self) -> None:
        assert category_matches(None, 2000)
        assert category_matches(None, 7000)

    def test_parent_covers_its_children(self) -> None:
        assert category_matches(5000, 5000)
        assert category_matches(5000, 5070)
        assert not category_matches(5000, 2000)

    def test_child_matches_only_itself(self) -> None:
        assert category_matches(5070, 5070)
        assert not category_matches(5070, 5000)
        assert not category_matches(2040, 2000)

    @pytest.mark.parametrize("base", [HttpxPluginBase, PlaywrightPluginBase])
    def test_plugin_bases_use_it(self, base: type) -> None:
        assert base._category_matches(5000, 5070)
        assert not base._category_matches(5070, 5000)


class TestServedCategory:
    def test_a_category_the_site_offers(self) -> None:
        assert served_category(2000, (2000, 5000, 5070)) == 2000
        assert served_category(5070, (2000, 5000, 5070)) == 5070
        assert served_category(5000, (5070,)) == 5000  # parent of an offered child

    def test_a_child_the_site_does_not_tell_apart_is_its_parent(self) -> None:
        # All films are labelled 2000: a Movies/HD request gets the films
        assert served_category(2040, (2000, 5000)) == 2000
        assert served_category(5080, (2000, 5000, 5070)) == 5000

    def test_a_family_the_site_does_not_serve(self) -> None:
        assert served_category(3000, (2000, 5000)) is None
        assert served_category(8000, (2000, 5000)) is None
        assert served_category(7020, (2000, 5000)) is None


class TestStreamCategory:
    def test_films_are_movies(self) -> None:
        assert stream_category(["Action", "Drama"], is_series=False) == 2000

    def test_animated_films_stay_movies(self) -> None:
        # They used to be 5070 (TV/Anime) and dropped out of movie searches
        assert stream_category(["Animation", "Action"], is_series=False) == 2000
        assert stream_category(["Anime"], is_series=False) == 2000

    def test_documentaries_stay_movies(self) -> None:
        assert stream_category(["Dokumentation"], is_series=False) == 2000

    def test_series(self) -> None:
        assert stream_category(["Crime", "Drama"], is_series=True) == 5000

    def test_anime_and_animation_series(self) -> None:
        assert stream_category(["Anime", "Action"], is_series=True) == 5070
        assert stream_category(["animation"], is_series=True) == 5070

    def test_stream_categories_are_the_possible_labels(self) -> None:
        assert set(STREAM_CATEGORIES) == {2000, 5000, 5070}


class TestIsSeriesTitle:
    @pytest.mark.parametrize(
        "title",
        [
            "Breaking.Bad.S01E01.German.DL.1080p",
            "Stranger.Things.S03.German.EAC3D.DL.2160p",
            "Breaking Bad S05E01 Live Free or Die",
            "Stranger Things (Staffel 3)",
            "Dark - Staffel 2 - Folge 1",
        ],
    )
    def test_series(self, title: str) -> None:
        assert is_series_title(title)

    @pytest.mark.parametrize(
        "title",
        [
            "The.Batman.2022.German.DL.1080p",
            "Season of the Witch 2011",
            "S1mone 2002",
            "Mission Impossible SuperS01",
        ],
    )
    def test_films(self, title: str) -> None:
        assert not is_series_title(title)


class TestFilterByCategory:
    def _result(self, title: str, category: int) -> SearchResult:
        return SearchResult(title=title, download_link="https://x", category=category)

    def test_keeps_the_matching_results(self) -> None:
        results = [
            self._result("Film", 2000),
            self._result("Series", 5000),
            self._result("Anime", 5070),
        ]

        assert [r.title for r in filter_by_category(results, 5000)] == [
            "Series",
            "Anime",
        ]
        assert [r.title for r in filter_by_category(results, 2000)] == ["Film"]
        assert [r.title for r in filter_by_category(results, 5070)] == ["Anime"]


_STREAM_PLUGINS = (
    "megakino",
    "streamcloud",
    "streamkiste",
    "kinoger",
    "movie2k",
    "hdfilme",
    "filmpalast_to",
    "kinoking",
)


def _load_plugin(name: str) -> Any:
    path = Path(__file__).resolve().parents[3] / "plugins" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_category_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.plugin


class TestStreamPlugins:
    """The film and series sites serve 2000 and 5000 only."""

    @pytest.mark.parametrize("name", _STREAM_PLUGINS)
    @pytest.mark.parametrize("category", [3000, 4000, 7020, 8000])
    async def test_a_category_the_site_does_not_serve(
        self, name: str, category: int
    ) -> None:
        plugin = _load_plugin(name)
        client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = client

        results = await plugin.search("batman", category=category)

        assert results == []
        client.get.assert_not_awaited()
        client.post.assert_not_awaited()
        client.head.assert_not_awaited()


class TestDownloadPlugins:
    """Sites with few categories answer the others without a request."""

    @pytest.mark.parametrize(
        ("name", "category"),
        [
            ("hdsource", 3000),  # films, series, games
            ("jjs", 4000),  # films, series
            ("movieblog", 7000),  # films, series
            ("serienjunkies", 2000),  # series only
        ],
    )
    async def test_a_category_the_site_does_not_serve(
        self, name: str, category: int
    ) -> None:
        plugin = _load_plugin(name)
        client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = client

        assert await plugin.search("batman", category=category) == []
        client.get.assert_not_awaited()
        client.post.assert_not_awaited()
        client.head.assert_not_awaited()
