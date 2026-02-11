"""Unit tests for the cine.to plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "cine.py"


@pytest.fixture()
def cine_mod():
    """Import cine plugin module."""
    spec = importlib.util.spec_from_file_location("cine", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cine"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("cine", None)


# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------

SEARCH_RESPONSE = {
    "status": True,
    "pages": 2,
    "current": 1,
    "genres": {"1": 5, "7": 3, "20": 2},
    "entries": [
        {
            "imdb": "1877830",
            "year": 2022,
            "language": 1,
            "quality": 3,
            "title": "The Batman",
        },
        {
            "imdb": "2313197",
            "year": 2005,
            "language": 2,
            "quality": 3,
            "title": "Batman Begins",
        },
    ],
}

SEARCH_RESPONSE_PAGE_2 = {
    "status": True,
    "pages": 2,
    "current": 2,
    "genres": {},
    "entries": [
        {
            "imdb": "0372784",
            "year": 2008,
            "language": 1,
            "quality": 3,
            "title": "The Dark Knight",
        },
    ],
}

SEARCH_RESPONSE_SINGLE_PAGE = {
    "status": True,
    "pages": 1,
    "current": 1,
    "genres": {"1": 2},
    "entries": [
        {
            "imdb": "1877830",
            "year": 2022,
            "language": 1,
            "quality": 3,
            "title": "The Batman",
        },
    ],
}

EMPTY_SEARCH_RESPONSE: dict = {
    "status": True,
    "pages": 0,
    "current": 0,
    "genres": {},
    "entries": [],
}

SEARCH_RESPONSE_STATUS_FALSE: dict = {
    "status": False,
}

DETAIL_RESPONSE = {
    "status": True,
    "entry": {
        "title": "The Batman",
        "plot_en": "Batman ventures into Gotham City's underworld...",
        "plot_de": "Batman ermittelt in Gotham Citys Unterwelt...",
        "trailer_en": "https://youtube.com/watch?v=abc",
        "trailer_de": "",
        "year": 2022,
        "date": "2022-03-01",
        "duration": 176,
        "rating": 7.8,
        "cover": "https://image.tmdb.org/poster.jpg",
        "lang": [1, 2],
        "genres": ["Action", "Crime", "Mystery"],
        "actor": ["Robert Pattinson", "Zoë Kravitz"],
        "producer": ["Matt Reeves"],
        "director": ["Matt Reeves"],
    },
}

DETAIL_RESPONSE_NO_PLOT_DE = {
    "status": True,
    "entry": {
        "title": "Some Movie",
        "plot_en": "An English-only plot.",
        "plot_de": "",
        "year": 2023,
        "duration": 120,
        "rating": 6.5,
        "cover": "",
        "genres": ["Drama"],
    },
}

DETAIL_RESPONSE_STATUS_FALSE: dict = {
    "status": False,
}

LINKS_RESPONSE = {
    "status": True,
    "links": {
        "voe": ["3", 438933, 439591],
        "vidoza": ["3", 438935],
    },
}

LINKS_RESPONSE_EMPTY: dict = {
    "status": True,
    "links": {},
}

LINKS_RESPONSE_STATUS_FALSE: dict = {
    "status": False,
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_json_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(data)
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, cine_mod):
        assert cine_mod.plugin.name == "cine"

    def test_version(self, cine_mod):
        assert cine_mod.plugin.version == "1.0.0"

    def test_mode(self, cine_mod):
        assert cine_mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Build search result tests
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    """Tests for _build_search_result method."""

    def test_movie_with_links(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]
        detail = DETAIL_RESPONSE["entry"]
        links = LINKS_RESPONSE["links"]

        sr = p._build_search_result(entry, detail, links)

        assert sr.title == "The Batman (2022)"
        assert sr.download_link == "https://cine.to/out/438933"
        assert sr.category == 2000
        assert sr.published_date == "2022"
        assert sr.download_links is not None
        assert len(sr.download_links) == 3  # voe×2 + vidoza×1

    def test_download_links_have_hoster_info(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]
        links = LINKS_RESPONSE["links"]

        sr = p._build_search_result(entry, None, links)

        assert sr.download_links[0]["hoster"] == "voe (HD)"
        assert sr.download_links[0]["link"] == "https://cine.to/out/438933"
        assert sr.download_links[1]["hoster"] == "voe (HD)"
        assert sr.download_links[1]["link"] == "https://cine.to/out/439591"
        assert sr.download_links[2]["hoster"] == "vidoza (HD)"
        assert sr.download_links[2]["link"] == "https://cine.to/out/438935"

    def test_no_detail_no_links_fallback(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]

        sr = p._build_search_result(entry, None, None)

        assert sr.title == "The Batman (2022)"
        assert "cine.to/#tt1877830" in sr.download_link
        assert sr.download_links is None
        assert sr.description is None

    def test_detail_german_plot_preferred(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]
        detail = DETAIL_RESPONSE["entry"]

        sr = p._build_search_result(entry, detail, None)

        assert "Gotham Citys Unterwelt" in sr.description

    def test_detail_english_plot_fallback(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = {"imdb": "9999999", "year": 2023, "title": "Some Movie"}
        detail = DETAIL_RESPONSE_NO_PLOT_DE["entry"]

        sr = p._build_search_result(entry, detail, None)

        assert "English-only plot" in sr.description

    def test_no_year(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = {"imdb": "1234567", "title": "Unknown Movie"}

        sr = p._build_search_result(entry, None, None)

        assert sr.title == "Unknown Movie"
        assert sr.published_date is None

    def test_metadata_fields(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]
        detail = DETAIL_RESPONSE["entry"]

        sr = p._build_search_result(entry, detail, None)

        assert sr.metadata["genres"] == "Action, Crime, Mystery"
        assert sr.metadata["imdb_id"] == "1877830"
        assert sr.metadata["rating"] == "7.8"
        assert sr.metadata["runtime"] == "176"
        assert sr.metadata["quality"] == "HD"

    def test_quality_mapping(self, cine_mod):
        p = cine_mod.CinePlugin()

        for code, label in [(0, "CAM"), (1, "TS"), (2, "DVD"), (3, "HD")]:
            entry = {
                "imdb": "1234567",
                "title": "Test",
                "year": 2023,
                "quality": code,
            }
            sr = p._build_search_result(entry, None, None)
            assert sr.metadata["quality"] == label

    def test_long_description_truncated(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = {"imdb": "1234567", "title": "Long Desc", "year": 2023}
        detail = {
            "plot_de": "A" * 500,
            "plot_en": "",
            "genres": [],
        }

        sr = p._build_search_result(entry, detail, None)

        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_empty_links_dict(self, cine_mod):
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]

        sr = p._build_search_result(entry, None, {})

        assert sr.download_links is None
        assert "cine.to/#tt" in sr.download_link

    def test_malformed_link_data_skipped(self, cine_mod):
        """Link data with fewer than 2 elements should be skipped."""
        p = cine_mod.CinePlugin()
        entry = SEARCH_RESPONSE["entries"][0]
        links = {"bad_hoster": ["3"]}  # Only quality code, no link IDs

        sr = p._build_search_result(entry, None, links)

        assert sr.download_links is None


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for CinePlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, cine_mod, mock_client):
        p = cine_mod.CinePlugin()
        p._client = mock_client
        p.base_url = "https://cine.to"
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, plugin, mock_client):
        search_resp = _make_json_response(SEARCH_RESPONSE_SINGLE_PAGE)
        detail_resp = _make_json_response(DETAIL_RESPONSE)
        links_resp = _make_json_response(LINKS_RESPONSE)

        async def mock_post(url, **kwargs):
            url_str = str(url)
            if "/request/search" in url_str:
                return search_resp
            if "/request/entry" in url_str:
                return detail_resp
            if "/request/links" in url_str:
                return links_resp
            return _make_json_response({})

        mock_client.post = AsyncMock(side_effect=mock_post)

        results = await plugin.search("batman")

        assert len(results) == 1
        assert results[0].title == "The Batman (2022)"
        assert results[0].category == 2000
        assert results[0].download_link == "https://cine.to/out/438933"

    @pytest.mark.asyncio
    async def test_search_pagination(self, plugin, mock_client):
        """Search fetches multiple pages when available."""
        page_count = 0

        async def mock_post(url, **kwargs):
            nonlocal page_count
            url_str = str(url)
            if "/request/search" in url_str:
                page_count += 1
                if page_count == 1:
                    return _make_json_response(SEARCH_RESPONSE)
                return _make_json_response(SEARCH_RESPONSE_PAGE_2)
            if "/request/entry" in url_str:
                return _make_json_response(DETAIL_RESPONSE)
            if "/request/links" in url_str:
                return _make_json_response(LINKS_RESPONSE)
            return _make_json_response({})

        mock_client.post = AsyncMock(side_effect=mock_post)

        results = await plugin.search("batman")

        assert page_count == 2
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_empty_query(self, plugin):
        results = await plugin.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_category_tv(self, plugin):
        results = await plugin.search("test", category=5000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_category_music(self, plugin):
        results = await plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_accepted_movie_category(self, plugin, mock_client):
        search_resp = _make_json_response(SEARCH_RESPONSE_SINGLE_PAGE)
        detail_resp = _make_json_response(DETAIL_RESPONSE)
        links_resp = _make_json_response(LINKS_RESPONSE)

        async def mock_post(url, **kwargs):
            url_str = str(url)
            if "/request/search" in url_str:
                return search_resp
            if "/request/entry" in url_str:
                return detail_resp
            if "/request/links" in url_str:
                return links_resp
            return _make_json_response({})

        mock_client.post = AsyncMock(side_effect=mock_post)

        results = await plugin.search("batman", category=2000)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin, mock_client):
        search_resp = _make_json_response(EMPTY_SEARCH_RESPONSE)
        mock_client.post = AsyncMock(return_value=search_resp)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_status_false(self, plugin, mock_client):
        search_resp = _make_json_response(SEARCH_RESPONSE_STATUS_FALSE)
        mock_client.post = AsyncMock(return_value=search_resp)

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin, mock_client):
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin.search("batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_failure_still_returns_result(
        self, plugin, mock_client
    ):
        """When detail/links fail, result uses search entry data only."""
        search_resp = _make_json_response(SEARCH_RESPONSE_SINGLE_PAGE)
        detail_fail = _make_json_response(DETAIL_RESPONSE_STATUS_FALSE)
        links_fail = _make_json_response(LINKS_RESPONSE_STATUS_FALSE)

        async def mock_post(url, **kwargs):
            url_str = str(url)
            if "/request/search" in url_str:
                return search_resp
            if "/request/entry" in url_str:
                return detail_fail
            if "/request/links" in url_str:
                return links_fail
            return _make_json_response({})

        mock_client.post = AsyncMock(side_effect=mock_post)

        results = await plugin.search("batman")

        assert len(results) == 1
        assert "cine.to/#tt" in results[0].download_link
        assert results[0].download_links is None

    @pytest.mark.asyncio
    async def test_detail_http_error_fallback(self, plugin, mock_client):
        """HTTP errors on detail/links still produce results."""
        search_resp = _make_json_response(SEARCH_RESPONSE_SINGLE_PAGE)

        async def mock_post(url, **kwargs):
            url_str = str(url)
            if "/request/search" in url_str:
                return search_resp
            raise httpx.ConnectError("Connection refused")

        mock_client.post = AsyncMock(side_effect=mock_post)

        results = await plugin.search("batman")

        assert len(results) == 1
        assert results[0].download_links is None

    @pytest.mark.asyncio
    async def test_entry_without_imdb_skipped(self, plugin, mock_client):
        """Entries without an imdb field should be skipped."""
        bad_search = {
            "status": True,
            "pages": 1,
            "current": 1,
            "genres": {},
            "entries": [
                {"title": "No IMDB Movie", "year": 2023},
            ],
        }
        search_resp = _make_json_response(bad_search)
        mock_client.post = AsyncMock(return_value=search_resp)

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_stops_on_last_page(self, plugin, mock_client):
        """Search stops paginating when current page >= total pages."""
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            url_str = str(url)
            if "/request/search" in url_str:
                call_count += 1
                return _make_json_response(SEARCH_RESPONSE_SINGLE_PAGE)
            if "/request/entry" in url_str:
                return _make_json_response(DETAIL_RESPONSE)
            if "/request/links" in url_str:
                return _make_json_response(LINKS_RESPONSE)
            return _make_json_response({})

        mock_client.post = AsyncMock(side_effect=mock_post)

        await plugin.search("batman")

        # Single-page response → only 1 search API call
        assert call_count == 1


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, cine_mod):
        p = cine_mod.CinePlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, cine_mod):
        p = cine_mod.CinePlugin()

        await p.cleanup()  # Should not raise
