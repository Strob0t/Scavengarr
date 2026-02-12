"""Unit tests for the moflix-stream.xyz plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "moflix.py"


@pytest.fixture()
def moflix_mod():
    """Import moflix plugin module."""
    spec = importlib.util.spec_from_file_location("moflix", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["moflix"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("moflix", None)


# ---------------------------------------------------------------------------
# JSON fixtures (same as API responses)
# ---------------------------------------------------------------------------

SEARCH_RESPONSE = {
    "results": [
        {
            "id": 2809,
            "name": "The Batman",
            "type": "movie",
            "release_date": "2022-03-01T00:00:00.000000Z",
            "description": "Ein düsterer Rachefeldzug...",
            "poster": "https://image.tmdb.org/t/p/original/poster.jpg",
            "backdrop": "https://image.tmdb.org/t/p/w1280/backdrop.jpg",
            "runtime": 176,
            "imdb_id": "tt1877830",
            "tmdb_id": 414906,
            "year": 2022,
            "rating": 7.7,
            "is_series": False,
            "vote_count": 10000,
            "certification": "pg13",
            "views": 500000,
            "popularity": 200,
            "model_type": "title",
        },
        {
            "id": 9232,
            "name": "Batman: Caped Crusader",
            "type": "movie",
            "release_date": "2024-08-01T00:00:00.000000Z",
            "description": "Eine animierte Batman-Serie...",
            "poster": "https://image.tmdb.org/t/p/original/poster2.jpg",
            "runtime": 25,
            "imdb_id": "tt15255200",
            "tmdb_id": 365448,
            "year": 2024,
            "rating": 7.5,
            "is_series": True,
            "vote_count": 500,
            "model_type": "title",
        },
    ],
}

DETAIL_MOVIE_RESPONSE = {
    "title": {
        "id": 2809,
        "name": "The Batman",
        "is_series": False,
        "year": 2022,
        "videos": [
            {
                "id": 456,
                "name": "Mirror 1",
                "src": "https://doods.to/e/abc123",
                "type": "embed",
                "quality": "1080p/5.1",
                "language": "de",
                "category": "full",
                "season_num": None,
                "episode_num": None,
            },
            {
                "id": 789,
                "name": "Mirror 2",
                "src": "https://moflix.upns.xyz/#xyz",
                "type": "embed",
                "quality": "1080p/5.1",
                "language": "de",
                "category": "full",
                "season_num": None,
                "episode_num": None,
            },
        ],
        "genres": [
            {"id": 1, "name": "thriller"},
            {"id": 2, "name": "krimi"},
            {"id": 3, "name": "mystery"},
        ],
    },
}

DETAIL_SERIES_RESPONSE = {
    "title": {
        "id": 9232,
        "name": "Batman: Caped Crusader",
        "is_series": True,
        "year": 2024,
        "videos": [],
        "genres": [
            {"id": 10, "name": "animation"},
            {"id": 11, "name": "action"},
        ],
    },
}

EMPTY_SEARCH_RESPONSE: dict = {
    "results": [],
}

DETAIL_NO_VIDEOS_RESPONSE = {
    "title": {
        "id": 9999,
        "name": "No Videos Title",
        "is_series": False,
        "year": 2023,
        "videos": [],
        "genres": [],
    },
}


# ---------------------------------------------------------------------------
# Helper: mock Playwright page
# ---------------------------------------------------------------------------


def _make_mock_page() -> AsyncMock:
    """Create a mock Playwright Page.

    ``is_closed()`` is synchronous in Playwright, so we use MagicMock
    to avoid returning a coroutine.
    """
    page = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    return page


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, moflix_mod):
        assert moflix_mod.plugin.name == "moflix"

    def test_version(self, moflix_mod):
        assert moflix_mod.plugin.version == "1.1.0"

    def test_mode(self, moflix_mod):
        assert moflix_mod.plugin.mode == "playwright"

    def test_provides(self, moflix_mod):
        assert moflix_mod.plugin.provides == "stream"


# ---------------------------------------------------------------------------
# Build search result tests
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    """Tests for _build_search_result method."""

    def test_movie_with_videos(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]
        detail = DETAIL_MOVIE_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.title == "The Batman (2022)"
        assert sr.download_link == "https://doods.to/e/abc123"
        assert sr.category == 2000
        assert sr.published_date == "2022"
        assert sr.download_links is not None
        assert len(sr.download_links) == 2

    def test_movie_download_links_have_hoster_info(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]
        detail = DETAIL_MOVIE_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.download_links[0]["hoster"] == "Mirror 1 (1080p/5.1)"
        assert sr.download_links[0]["link"] == "https://doods.to/e/abc123"
        assert sr.download_links[1]["hoster"] == "Mirror 2 (1080p/5.1)"

    def test_series_category(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][1]
        detail = DETAIL_SERIES_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.category == 5000
        assert "2024" in sr.title

    def test_no_detail_fallback(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert sr.title == "The Batman (2022)"
        # Download link falls back to source URL
        assert "moflix-stream.xyz/titles/2809" in sr.download_link
        assert sr.download_links is None

    def test_no_videos_fallback(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = {
            "id": 9999,
            "name": "No Videos",
            "year": 2023,
            "is_series": False,
        }
        detail = DETAIL_NO_VIDEOS_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert "moflix-stream.xyz/titles/9999" in sr.download_link
        assert sr.download_links is None

    def test_no_year(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = {"id": 1, "name": "Unknown Movie", "is_series": False}

        sr = p._build_search_result(entry, None)

        assert sr.title == "Unknown Movie"
        assert sr.published_date is None

    def test_metadata_fields(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]
        detail = DETAIL_MOVIE_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.metadata["genres"] == "thriller, krimi, mystery"
        assert sr.metadata["imdb_id"] == "tt1877830"
        assert sr.metadata["tmdb_id"] == "414906"
        assert sr.metadata["rating"] == "7.7"
        assert sr.metadata["runtime"] == "176"

    def test_long_description_truncated(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = {
            "id": 1,
            "name": "Long Desc",
            "year": 2023,
            "is_series": False,
            "description": "A" * 500,
        }

        sr = p._build_search_result(entry, None)

        assert len(sr.description) == 300
        assert sr.description.endswith("...")


# ---------------------------------------------------------------------------
# Plugin search tests (mocked Playwright page.evaluate)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for MoflixPlugin.search() with mocked Playwright."""

    @pytest.fixture()
    def plugin(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p._domain_verified = True
        p.base_url = "https://moflix-stream.xyz"
        mock_page = _make_mock_page()
        p._page = mock_page
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, plugin):
        async def mock_evaluate(js, arg=None):
            url = str(arg) if arg else ""
            if "/api/v1/search/" in url:
                return SEARCH_RESPONSE
            if "/api/v1/titles/2809" in url:
                return DETAIL_MOVIE_RESPONSE
            if "/api/v1/titles/9232" in url:
                return DETAIL_SERIES_RESPONSE
            return {}

        plugin._page.evaluate = AsyncMock(side_effect=mock_evaluate)

        results = await plugin.search("batman")

        assert len(results) == 2
        assert results[0].title == "The Batman (2022)"
        assert results[0].category == 2000
        assert results[1].category == 5000

    @pytest.mark.asyncio
    async def test_search_empty_query(self, plugin):
        results = await plugin.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_category(self, plugin):
        # Music category (3000) not supported
        results = await plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_movie_category_filters_series(self, plugin):
        async def mock_evaluate(js, arg=None):
            url = str(arg) if arg else ""
            if "/api/v1/search/" in url:
                return SEARCH_RESPONSE
            if "/api/v1/titles/2809" in url:
                return DETAIL_MOVIE_RESPONSE
            return {}

        plugin._page.evaluate = AsyncMock(side_effect=mock_evaluate)

        # Request movies only (2000) - should filter out the series result
        results = await plugin.search("batman", category=2000)

        assert len(results) == 1
        assert results[0].category == 2000

    @pytest.mark.asyncio
    async def test_search_tv_category_filters_movies(self, plugin):
        async def mock_evaluate(js, arg=None):
            url = str(arg) if arg else ""
            if "/api/v1/search/" in url:
                return SEARCH_RESPONSE
            if "/api/v1/titles/9232" in url:
                return DETAIL_SERIES_RESPONSE
            return {}

        plugin._page.evaluate = AsyncMock(side_effect=mock_evaluate)

        # Request TV only (5000) - should filter out movie results
        results = await plugin.search("batman", category=5000)

        assert len(results) == 1
        assert results[0].category == 5000

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin):
        plugin._page.evaluate = AsyncMock(return_value=EMPTY_SEARCH_RESPONSE)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_evaluate_error(self, plugin):
        plugin._page.evaluate = AsyncMock(side_effect=Exception("page crashed"))

        results = await plugin.search("batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_failure_uses_fallback(self, plugin):
        """When a detail page fails, result uses search entry data only."""
        call_count = 0

        async def mock_evaluate(js, arg=None):
            nonlocal call_count
            call_count += 1
            url = str(arg) if arg else ""
            if "/api/v1/search/" in url:
                return SEARCH_RESPONSE
            # Detail calls fail
            return {"_error": 500}

        plugin._page.evaluate = AsyncMock(side_effect=mock_evaluate)

        results = await plugin.search("batman")

        assert len(results) == 2
        # Fallback: no videos, download_link is source URL
        assert "moflix-stream.xyz/titles/" in results[0].download_link
        assert results[0].download_links is None

    @pytest.mark.asyncio
    async def test_search_with_videos_in_download_link(self, plugin):
        async def mock_evaluate(js, arg=None):
            url = str(arg) if arg else ""
            if "/api/v1/search/" in url:
                return SEARCH_RESPONSE
            if "/api/v1/titles/2809" in url:
                return DETAIL_MOVIE_RESPONSE
            if "/api/v1/titles/9232" in url:
                return DETAIL_SERIES_RESPONSE
            return {}

        plugin._page.evaluate = AsyncMock(side_effect=mock_evaluate)

        results = await plugin.search("batman")

        # First result (movie with videos) should have video embed as link
        movie = results[0]
        assert movie.download_link == "https://doods.to/e/abc123"

    @pytest.mark.asyncio
    async def test_entry_without_id_skipped(self, plugin):
        """Entries without an id should be skipped."""
        bad_search = {
            "results": [
                {"name": "No ID Movie", "is_series": False},
            ],
        }
        plugin._page.evaluate = AsyncMock(return_value=bad_search)

        results = await plugin.search("test")

        assert results == []


# ---------------------------------------------------------------------------
# Domain verification tests
# ---------------------------------------------------------------------------


class TestDomainVerification:
    """Tests for domain fallback logic."""

    @pytest.mark.asyncio
    async def test_skips_if_already_verified(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p._domain_verified = True
        p.base_url = "https://custom.domain"

        await p._verify_domain()

        assert p.base_url == "https://custom.domain"


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_browser(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        mock_page = _make_mock_page()
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_pw = AsyncMock()

        p._page = mock_page
        p._context = mock_context
        p._browser = mock_browser
        p._pw = mock_pw

        await p.cleanup()

        mock_page.close.assert_awaited_once()
        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert p._page is None
        assert p._context is None
        assert p._browser is None
        assert p._pw is None

    @pytest.mark.asyncio
    async def test_cleanup_without_browser(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()

        await p.cleanup()  # Should not raise
