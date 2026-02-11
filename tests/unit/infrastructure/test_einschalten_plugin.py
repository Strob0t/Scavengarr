"""Unit tests for the einschalten.in plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "einschalten.py"


@pytest.fixture()
def einschalten_mod():
    """Import einschalten plugin module."""
    spec = importlib.util.spec_from_file_location("einschalten", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["einschalten"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("einschalten", None)


# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------

SEARCH_RESPONSE = {
    "data": [
        {
            "id": 268,
            "title": "Batman",
            "releaseDate": "1989-06-23",
            "posterPath": "/cij4dd21v2Rk2YtUQbV5kW69WB2.jpg",
            "voteAverage": 7.2,
            "collectionId": 948485,
        },
        {
            "id": 414906,
            "title": "The Batman",
            "releaseDate": "2022-03-01",
            "posterPath": "/74xTEgt7R36Fpooo50r9T25onhq.jpg",
            "voteAverage": 7.7,
            "collectionId": None,
        },
    ],
    "pagination": {
        "hasMore": False,
        "currentPage": 1,
    },
}

SEARCH_RESPONSE_PAGE1 = {
    "data": [
        {
            "id": 100,
            "title": "Movie A",
            "releaseDate": "2020-01-01",
            "posterPath": "/a.jpg",
            "voteAverage": 6.0,
            "collectionId": None,
        },
    ],
    "pagination": {
        "hasMore": True,
        "currentPage": 1,
    },
}

SEARCH_RESPONSE_PAGE2 = {
    "data": [
        {
            "id": 200,
            "title": "Movie B",
            "releaseDate": "2021-01-01",
            "posterPath": "/b.jpg",
            "voteAverage": 7.0,
            "collectionId": None,
        },
    ],
    "pagination": {
        "hasMore": False,
        "currentPage": 2,
    },
}

SEARCH_RESPONSE_DUPLICATE = {
    "data": [
        {
            "id": 100,
            "title": "Movie A",
            "releaseDate": "2020-01-01",
            "posterPath": "/a.jpg",
            "voteAverage": 6.0,
            "collectionId": None,
        },
    ],
    "pagination": {
        "hasMore": True,
        "currentPage": 2,
    },
}

DETAIL_BATMAN = {
    "id": 268,
    "title": "Batman",
    "tagline": "Have you ever danced with the devil in the pale moonlight?",
    "overview": "Batman must face his most ruthless nemesis when a deformed madman...",
    "releaseDate": "1989-06-23",
    "runtime": 126,
    "posterPath": "/cij4dd21v2Rk2YtUQbV5kW69WB2.jpg",
    "backdropPath": "/backdrop.jpg",
    "voteAverage": 7.2,
    "voteCount": 5000,
    "collectionId": 948485,
    "imdbId": "tt0096895",
    "genres": [
        {"id": 28, "name": "Action"},
        {"id": 14, "name": "Fantasy"},
    ],
}

DETAIL_THE_BATMAN = {
    "id": 414906,
    "title": "The Batman",
    "tagline": "Unmask the truth.",
    "overview": "In his second year of fighting crime, Batman uncovers corruption...",
    "releaseDate": "2022-03-01",
    "runtime": 176,
    "posterPath": "/74xTEgt7R36Fpooo50r9T25onhq.jpg",
    "backdropPath": "/backdrop2.jpg",
    "voteAverage": 7.7,
    "voteCount": 10000,
    "collectionId": None,
    "imdbId": "tt1877830",
    "genres": [
        {"id": 80, "name": "Krimi"},
        {"id": 9648, "name": "Mystery"},
        {"id": 53, "name": "Thriller"},
    ],
}

WATCH_BATMAN = {
    "releaseName": "Batman.1989.Remastered.German.720p.BluRay.x264-CONTRiBUTiON",
    "streamUrl": "https://vide0.net/e/qq9v0x1hed9r",
}

WATCH_THE_BATMAN = {
    "releaseName": "The.Batman.2022.German.DL.1080p.BluRay.x264-DETAiLS",
    "streamUrl": "https://doodstream.com/e/abc123",
}

EMPTY_SEARCH_RESPONSE: dict = {
    "data": [],
    "pagination": {
        "hasMore": False,
        "currentPage": 1,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_json_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(data)
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestExtractYear:
    """Tests for _extract_year helper."""

    def test_valid_date(self, einschalten_mod):
        assert einschalten_mod._extract_year("2022-03-01") == "2022"

    def test_year_only(self, einschalten_mod):
        assert einschalten_mod._extract_year("1989") == "1989"

    def test_none(self, einschalten_mod):
        assert einschalten_mod._extract_year(None) is None

    def test_empty_string(self, einschalten_mod):
        assert einschalten_mod._extract_year("") is None

    def test_short_string(self, einschalten_mod):
        assert einschalten_mod._extract_year("abc") is None

    def test_non_digit_prefix(self, einschalten_mod):
        assert einschalten_mod._extract_year("abcd-01-01") is None


class TestHosterFromUrl:
    """Tests for _hoster_from_url helper."""

    def test_normal_url(self, einschalten_mod):
        assert (
            einschalten_mod._hoster_from_url("https://vide0.net/e/abc") == "vide0.net"
        )

    def test_doodstream(self, einschalten_mod):
        assert (
            einschalten_mod._hoster_from_url("https://doodstream.com/e/xyz")
            == "doodstream.com"
        )

    def test_invalid_url(self, einschalten_mod):
        assert einschalten_mod._hoster_from_url("not-a-url") == "Stream"

    def test_empty_url(self, einschalten_mod):
        assert einschalten_mod._hoster_from_url("") == "Stream"


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, einschalten_mod):
        assert einschalten_mod.plugin.name == "einschalten"

    def test_version(self, einschalten_mod):
        assert einschalten_mod.plugin.version == "1.0.0"

    def test_mode(self, einschalten_mod):
        assert einschalten_mod.plugin.mode == "httpx"

    def test_base_url(self, einschalten_mod):
        assert einschalten_mod.plugin.base_url == "https://einschalten.in"


# ---------------------------------------------------------------------------
# Build search result tests
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    """Tests for _build_search_result method."""

    def test_movie_with_watch_and_detail(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        sr = p._build_search_result(entry, DETAIL_BATMAN, WATCH_BATMAN)

        assert sr.title == "Batman (1989)"
        assert sr.download_link == "https://vide0.net/e/qq9v0x1hed9r"
        assert sr.category == 2000
        assert sr.published_date == "1989"
        assert (
            sr.release_name
            == "Batman.1989.Remastered.German.720p.BluRay.x264-CONTRiBUTiON"
        )
        assert sr.source_url == "https://einschalten.in/movies/268"

    def test_download_links_contain_hoster(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        sr = p._build_search_result(entry, DETAIL_BATMAN, WATCH_BATMAN)

        assert sr.download_links is not None
        assert len(sr.download_links) == 1
        assert sr.download_links[0]["hoster"] == "vide0.net"
        assert sr.download_links[0]["link"] == "https://vide0.net/e/qq9v0x1hed9r"

    def test_metadata_fields(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        sr = p._build_search_result(entry, DETAIL_BATMAN, WATCH_BATMAN)

        assert sr.metadata["genres"] == "Action, Fantasy"
        assert sr.metadata["imdb_id"] == "tt0096895"
        assert sr.metadata["tmdb_id"] == "268"
        assert sr.metadata["rating"] == "7.2"
        assert sr.metadata["runtime"] == "126"

    def test_no_detail_fallback(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        sr = p._build_search_result(entry, None, WATCH_BATMAN)

        assert sr.title == "Batman (1989)"
        assert sr.download_link == "https://vide0.net/e/qq9v0x1hed9r"
        assert sr.metadata["genres"] == ""
        assert sr.metadata["imdb_id"] == ""

    def test_no_watch_fallback(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        sr = p._build_search_result(entry, DETAIL_BATMAN, None)

        assert sr.download_link == "https://einschalten.in/movies/268"
        assert sr.download_links is None
        assert sr.release_name is None

    def test_no_detail_no_watch(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        sr = p._build_search_result(entry, None, None)

        assert sr.title == "Batman (1989)"
        assert sr.download_link == "https://einschalten.in/movies/268"
        assert sr.download_links is None
        assert sr.description is None

    def test_no_year(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = {"id": 1, "title": "Unknown Movie"}
        sr = p._build_search_result(entry, None, None)

        assert sr.title == "Unknown Movie"
        assert sr.published_date is None

    def test_long_description_truncated(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        long_detail = {
            **DETAIL_BATMAN,
            "overview": "A" * 500,
        }
        sr = p._build_search_result(entry, long_detail, None)

        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_empty_watch_fields(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = SEARCH_RESPONSE["data"][0]
        watch = {"releaseName": "", "streamUrl": ""}
        sr = p._build_search_result(entry, None, watch)

        assert sr.download_link == "https://einschalten.in/movies/268"
        assert sr.download_links is None
        assert sr.release_name is None

    def test_poster_from_detail_overrides_search(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = {
            "id": 1,
            "title": "Test",
            "releaseDate": "2020-01-01",
            "posterPath": "/search_poster.jpg",
        }
        detail = {
            "posterPath": "/detail_poster.jpg",
            "genres": [],
        }
        sr = p._build_search_result(entry, detail, None)

        assert sr.metadata["poster"] == "/detail_poster.jpg"

    def test_poster_from_search_when_detail_missing(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        entry = {
            "id": 1,
            "title": "Test",
            "releaseDate": "2020-01-01",
            "posterPath": "/search_poster.jpg",
        }
        sr = p._build_search_result(entry, None, None)

        assert sr.metadata["poster"] == "/search_poster.jpg"


# ---------------------------------------------------------------------------
# Search pagination tests
# ---------------------------------------------------------------------------


class TestApiSearch:
    """Tests for _api_search pagination and deduplication."""

    @pytest.fixture()
    def plugin(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        p._client = AsyncMock(spec=httpx.AsyncClient)
        return p

    @pytest.mark.asyncio
    async def test_single_page(self, plugin):
        resp = _make_json_response(SEARCH_RESPONSE)
        plugin._client.post = AsyncMock(return_value=resp)

        results = await plugin._api_search("batman")

        assert len(results) == 2
        plugin._client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multi_page(self, plugin):
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_json_response(SEARCH_RESPONSE_PAGE1)
            return _make_json_response(SEARCH_RESPONSE_PAGE2)

        plugin._client.post = AsyncMock(side_effect=mock_post)

        results = await plugin._api_search("test")

        assert len(results) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_stops_on_duplicate_results(self, plugin):
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_json_response(SEARCH_RESPONSE_PAGE1)
            return _make_json_response(SEARCH_RESPONSE_DUPLICATE)

        plugin._client.post = AsyncMock(side_effect=mock_post)

        results = await plugin._api_search("test")

        assert len(results) == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_empty_results(self, plugin):
        resp = _make_json_response(EMPTY_SEARCH_RESPONSE)
        plugin._client.post = AsyncMock(return_value=resp)

        results = await plugin._api_search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self, plugin):
        plugin._client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin._api_search("batman")

        assert results == []


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for EinschaltenPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, einschalten_mod, mock_client):
        p = einschalten_mod.EinschaltenPlugin()
        p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, plugin, mock_client):
        search_resp = _make_json_response(SEARCH_RESPONSE)
        detail_batman_resp = _make_json_response(DETAIL_BATMAN)
        detail_the_batman_resp = _make_json_response(DETAIL_THE_BATMAN)
        watch_batman_resp = _make_json_response(WATCH_BATMAN)
        watch_the_batman_resp = _make_json_response(WATCH_THE_BATMAN)

        async def mock_post(url, **kwargs):
            return search_resp

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/api/movies/268/watch" in url_str:
                return watch_batman_resp
            if "/api/movies/414906/watch" in url_str:
                return watch_the_batman_resp
            if "/api/movies/268" in url_str:
                return detail_batman_resp
            if "/api/movies/414906" in url_str:
                return detail_the_batman_resp
            return _make_json_response({})

        mock_client.post = AsyncMock(side_effect=mock_post)
        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 2
        assert results[0].title == "Batman (1989)"
        assert results[0].download_link == "https://vide0.net/e/qq9v0x1hed9r"
        assert results[0].category == 2000
        assert results[1].title == "The Batman (2022)"
        assert results[1].download_link == "https://doodstream.com/e/abc123"

    @pytest.mark.asyncio
    async def test_search_empty_query(self, plugin):
        results = await plugin.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_tv_category(self, plugin):
        results = await plugin.search("batman", category=5000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_music_category(self, plugin):
        results = await plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_accepts_movie_category(self, plugin, mock_client):
        search_resp = _make_json_response(SEARCH_RESPONSE)
        detail_resp = _make_json_response(DETAIL_BATMAN)
        watch_resp = _make_json_response(WATCH_BATMAN)

        mock_client.post = AsyncMock(return_value=search_resp)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/watch" in url_str:
                return watch_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman", category=2000)

        assert len(results) == 2
        assert all(r.category == 2000 for r in results)

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin, mock_client):
        search_resp = _make_json_response(EMPTY_SEARCH_RESPONSE)
        mock_client.post = AsyncMock(return_value=search_resp)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin, mock_client):
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin.search("batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_failure_still_returns_result(self, plugin, mock_client):
        """When detail/watch fail, result uses search entry data only."""
        search_resp = _make_json_response(SEARCH_RESPONSE)

        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin.search("batman")

        assert len(results) == 2
        assert "einschalten.in/movies/" in results[0].download_link
        assert results[0].download_links is None
        assert results[0].release_name is None

    @pytest.mark.asyncio
    async def test_entry_without_id_skipped(self, plugin, mock_client):
        bad_search = {
            "data": [
                {"title": "No ID Movie"},
            ],
            "pagination": {"hasMore": False, "currentPage": 1},
        }
        search_resp = _make_json_response(bad_search)
        mock_client.post = AsyncMock(return_value=search_resp)

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_watch_returns_release_name(self, plugin, mock_client):
        search_resp = _make_json_response(
            {
                "data": [SEARCH_RESPONSE["data"][0]],
                "pagination": {"hasMore": False, "currentPage": 1},
            }
        )
        detail_resp = _make_json_response(DETAIL_BATMAN)
        watch_resp = _make_json_response(WATCH_BATMAN)

        mock_client.post = AsyncMock(return_value=search_resp)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/watch" in url_str:
                return watch_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 1
        assert (
            results[0].release_name
            == "Batman.1989.Remastered.German.720p.BluRay.x264-CONTRiBUTiON"
        )


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, einschalten_mod):
        p = einschalten_mod.EinschaltenPlugin()

        await p.cleanup()  # Should not raise
