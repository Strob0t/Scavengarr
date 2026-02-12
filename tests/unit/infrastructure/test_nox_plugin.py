"""Unit tests for the nox.to plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "nox.py"


@pytest.fixture()
def nox_mod():
    """Import nox plugin module."""
    spec = importlib.util.spec_from_file_location("nox", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nox"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("nox", None)


# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------

SEARCH_RESPONSE = {
    "result": {
        "releases": [
            {
                "slug": "iron-man-2008-german-dl-1080p-bluray-x264-abc123",
                "type": "movie",
                "createdAt": "2025-06-15T10:00:00.000Z",
                "updatedAt": "2025-06-15T12:00:00.000Z",
                "codec": "x264",
                "sizeunit": "MB",
                "size": "1481",
                "audio": "DL AC3 5.1",
                "video": "BluRay",
                "name": "Iron.Man.2008.German.DL.1080p.BluRay.x264-GROUP",
                "title": "Iron Man",
                "languages": [],
                "publishat": "2025-06-15T10:30:00.000Z",
                "tags": ["hd"],
            },
            {
                "slug": "iron-man-2-2010-german-dl-720p-web-h264-def456",
                "type": "movie",
                "createdAt": "2025-06-14T08:00:00.000Z",
                "updatedAt": "2025-06-14T09:00:00.000Z",
                "codec": "h264",
                "sizeunit": "GB",
                "size": "4",
                "audio": "DL EAC3 5.1",
                "video": "Web",
                "name": "Iron.Man.2.2010.German.DL.720p.WEB.h264-OTHER",
                "title": "Iron Man 2",
                "languages": [],
                "publishat": "2025-06-14T08:30:00.000Z",
                "tags": [],
            },
        ],
        "media": [
            {
                "id": "abc123",
                "slug": "iron-man-media-slug",
                "title": "Iron Man",
                "type": "movie",
                "productionyear": 2008,
                "productioncountry": ["USA"],
                "description": "Tony Stark builds a high-tech suit of armor.",
                "duration": 126,
                "imdbrating": "7.9",
                "imdbid": "tt0371746",
                "genres": ["Action", "Adventure", "Sci-Fi"],
                "cover": "abc123cover.jpg",
            },
        ],
        "actor": [],
        "director": [],
        "writer": [],
        "author": [],
        "artist": [],
    }
}

SEARCH_RESPONSE_WITH_EPISODE = {
    "result": {
        "releases": [
            {
                "slug": "breaking-bad-s01e01-german-web",
                "type": "episode",
                "createdAt": "2025-05-01T10:00:00.000Z",
                "codec": "h264",
                "sizeunit": "MB",
                "size": "800",
                "audio": "DL AC3",
                "video": "Web",
                "name": "Breaking.Bad.S01E01.German.DL.720p.WEB.h264-GRP",
                "title": "Breaking Bad",
                "languages": [],
                "publishat": "2025-05-01T10:00:00.000Z",
                "tags": ["hd"],
            },
        ],
        "media": [
            {
                "id": "bb123",
                "slug": "breaking-bad-slug",
                "title": "Breaking Bad",
                "type": "episode",
                "productionyear": 2008,
                "description": "A chemistry teacher turns to crime.",
                "duration": 49,
                "imdbrating": "9.5",
                "imdbid": "tt0903747",
                "genres": ["Crime", "Drama", "Thriller"],
                "cover": "bb_cover.jpg",
            },
        ],
        "actor": [],
        "director": [],
        "writer": [],
        "author": [],
        "artist": [],
    }
}

SEARCH_RESPONSE_GAME_ONLY = {
    "result": {
        "releases": [
            {
                "slug": "some-game-release",
                "type": "game",
                "createdAt": "2025-06-01T10:00:00.000Z",
                "size": "52245",
                "sizeunit": "MB",
                "name": "Some.Game.Release",
                "title": "Some Game",
                "languages": [],
                "publishat": "2025-06-01T10:00:00.000Z",
                "tags": [],
            },
        ],
        "media": [],
        "actor": [],
        "director": [],
        "writer": [],
        "author": [],
        "artist": [],
    }
}

EMPTY_SEARCH_RESPONSE = {
    "result": {
        "releases": [],
        "media": [],
        "actor": [],
        "director": [],
        "writer": [],
        "author": [],
        "artist": [],
    }
}

BROWSE_RESPONSE = {
    "result": [
        {
            "slug": "latest-movie-release-slug",
            "type": "movie",
            "createdAt": "2025-06-16T10:00:00.000Z",
            "codec": "x264",
            "sizeunit": "MB",
            "size": "2000",
            "audio": "DL AC3 5.1",
            "video": "BluRay",
            "name": "Latest.Movie.2025.German.DL.1080p.BluRay.x264-GRP",
            "title": "Latest Movie",
            "languages": [],
            "publishat": "2025-06-16T10:00:00.000Z",
            "tags": ["hd"],
            "_media": {
                "slug": "latest-movie-media",
                "title": "Latest Movie",
                "type": "movie",
                "imdbrating": "7.5",
                "imdbid": "tt1234567",
                "cover": "latest_cover.jpg",
                "authors": [],
            },
        },
        {
            "slug": "latest-episode-release-slug",
            "type": "episode",
            "createdAt": "2025-06-16T09:00:00.000Z",
            "codec": "h264",
            "sizeunit": "MB",
            "size": "500",
            "audio": "DL EAC3",
            "video": "Web",
            "name": "Latest.Series.S02E05.German.DL.720p.WEB.h264-GRP",
            "title": "Latest Series",
            "languages": [],
            "publishat": "2025-06-16T09:00:00.000Z",
            "tags": ["hd"],
            "_media": {
                "slug": "latest-series-media",
                "title": "Latest Series",
                "type": "episode",
                "imdbrating": "8.0",
                "imdbid": "tt7654321",
                "cover": "series_cover.jpg",
                "authors": [],
            },
        },
    ]
}

BROWSE_RESPONSE_SMALL = {
    "result": [
        {
            "slug": "small-release",
            "type": "movie",
            "createdAt": "2025-06-16T10:00:00.000Z",
            "size": "1000",
            "sizeunit": "MB",
            "name": "Small.Movie.Release",
            "title": "Small Movie",
            "languages": [],
            "publishat": "2025-06-16T10:00:00.000Z",
            "tags": [],
            "_media": {
                "slug": "small-media",
                "title": "Small Movie",
                "type": "movie",
                "imdbrating": "6.0",
                "imdbid": "tt0000001",
                "cover": "",
                "authors": [],
            },
        },
    ]
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
    resp.url = "https://nox.to/api/test"
    return resp


def _make_plugin(mod) -> object:
    """Create a fresh NoxPlugin with domain verification skipped."""
    p = mod.NoxPlugin()
    p._domain_verified = True
    return p


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatSize:
    """Tests for _format_size helper."""

    def test_normal(self, nox_mod):
        assert nox_mod._format_size("1481", "MB") == "1481 MB"

    def test_gb(self, nox_mod):
        assert nox_mod._format_size("4", "GB") == "4 GB"

    def test_none_size(self, nox_mod):
        assert nox_mod._format_size(None, "MB") is None

    def test_empty_size(self, nox_mod):
        assert nox_mod._format_size("", "MB") is None

    def test_none_unit_defaults_to_mb(self, nox_mod):
        assert nox_mod._format_size("100", None) == "100 MB"


class TestCategoryForType:
    """Tests for _category_for_type helper."""

    def test_movie(self, nox_mod):
        assert nox_mod._category_for_type("movie") == 2000

    def test_episode(self, nox_mod):
        assert nox_mod._category_for_type("episode") == 5000

    def test_game_unsupported(self, nox_mod):
        assert nox_mod._category_for_type("game") is None

    def test_unknown_type(self, nox_mod):
        assert nox_mod._category_for_type("audiobook") is None


class TestExtractYear:
    """Tests for _extract_year helper."""

    def test_year_in_scene_name(self, nox_mod):
        assert nox_mod._extract_year("Iron.Man.2008.German.DL") == "2008"

    def test_year_in_title(self, nox_mod):
        assert nox_mod._extract_year("Iron Man [2008]") == "2008"

    def test_no_year(self, nox_mod):
        assert nox_mod._extract_year("Iron Man") is None

    def test_empty(self, nox_mod):
        assert nox_mod._extract_year("") is None


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestNoxPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, nox_mod):
        assert nox_mod.plugin.name == "nox"

    def test_provides(self, nox_mod):
        assert nox_mod.plugin.provides == "download"

    def test_default_language(self, nox_mod):
        assert nox_mod.plugin.default_language == "de"

    def test_base_url(self, nox_mod):
        assert nox_mod.plugin.base_url == "https://nox.to"

    def test_domains(self, nox_mod):
        assert nox_mod.plugin._domains == ["nox.to", "nox.tv"]

    def test_mode(self, nox_mod):
        assert nox_mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Build result tests
# ---------------------------------------------------------------------------


class TestBuildResult:
    """Tests for _build_result method."""

    def test_movie_with_media(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE["result"]["releases"][0]
        media = SEARCH_RESPONSE["result"]["media"][0]

        sr = p._build_result(release, media)

        assert sr is not None
        assert sr.title == "Iron Man (2008)"
        assert sr.download_link == "https://nox.to/release/iron-man-2008-german-dl-1080p-bluray-x264-abc123"
        assert sr.category == 2000
        assert sr.release_name == "Iron.Man.2008.German.DL.1080p.BluRay.x264-GROUP"
        assert sr.size == "1481 MB"
        assert sr.published_date == "2025-06-15"

    def test_metadata_fields(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE["result"]["releases"][0]
        media = SEARCH_RESPONSE["result"]["media"][0]

        sr = p._build_result(release, media)

        assert sr.metadata["imdb_id"] == "tt0371746"
        assert sr.metadata["rating"] == "7.9"
        assert sr.metadata["genres"] == "Action, Adventure, Sci-Fi"
        assert sr.metadata["runtime"] == "126"
        assert sr.metadata["codec"] == "x264"
        assert sr.metadata["video"] == "BluRay"
        assert sr.metadata["audio"] == "DL AC3 5.1"
        assert "abc123cover.jpg" in sr.metadata["poster"]

    def test_movie_without_media(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE["result"]["releases"][0]

        sr = p._build_result(release, None)

        assert sr is not None
        assert sr.title == "Iron Man (2008)"  # Year from release name
        assert sr.metadata["imdb_id"] == ""
        assert sr.metadata["genres"] == ""

    def test_episode_result(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE_WITH_EPISODE["result"]["releases"][0]
        media = SEARCH_RESPONSE_WITH_EPISODE["result"]["media"][0]

        sr = p._build_result(release, media)

        assert sr is not None
        assert sr.category == 5000
        assert "Breaking Bad" in sr.title

    def test_game_returns_none(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE_GAME_ONLY["result"]["releases"][0]

        sr = p._build_result(release, None)

        assert sr is None

    def test_no_slug_returns_none(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = {"slug": "", "type": "movie", "title": "Test"}

        sr = p._build_result(release, None)

        assert sr is None

    def test_no_title_returns_none(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = {"slug": "test-slug", "type": "movie", "title": ""}

        sr = p._build_result(release, None)

        assert sr is None

    def test_long_description_truncated(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE["result"]["releases"][0]
        media = {**SEARCH_RESPONSE["result"]["media"][0], "description": "A" * 500}

        sr = p._build_result(release, media)

        assert sr.description is not None
        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_browse_result_with_embedded_media(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = BROWSE_RESPONSE["result"][0]
        media = release["_media"]

        sr = p._build_result(release, media)

        assert sr is not None
        assert sr.title == "Latest Movie (2025)"  # Year from scene name
        assert sr.metadata["imdb_id"] == "tt1234567"
        assert sr.metadata["rating"] == "7.5"

    def test_size_gb_format(self, nox_mod):
        p = _make_plugin(nox_mod)
        release = SEARCH_RESPONSE["result"]["releases"][1]

        sr = p._build_result(release, None)

        assert sr is not None
        assert sr.size == "4 GB"

    def test_missing_codec_fields(self, nox_mod):
        """Game-like releases may lack codec/video/audio fields."""
        p = _make_plugin(nox_mod)
        release = {
            "slug": "no-codec-release",
            "type": "movie",
            "title": "No Codec Movie",
            "size": "1000",
            "sizeunit": "MB",
            "createdAt": "2025-01-01T00:00:00.000Z",
        }

        sr = p._build_result(release, None)

        assert sr is not None
        assert sr.metadata["codec"] == ""
        assert sr.metadata["video"] == ""
        assert sr.metadata["audio"] == ""


# ---------------------------------------------------------------------------
# Search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestNoxSearch:
    """Tests for NoxPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, nox_mod, mock_client):
        p = _make_plugin(nox_mod)
        p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, plugin, mock_client):
        resp = _make_json_response(SEARCH_RESPONSE)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("Iron Man")

        assert len(results) == 2
        assert results[0].title == "Iron Man (2008)"
        assert results[0].category == 2000
        assert results[1].title == "Iron Man 2 (2010)"
        assert results[1].category == 2000

    @pytest.mark.asyncio
    async def test_search_enriches_with_media(self, plugin, mock_client):
        resp = _make_json_response(SEARCH_RESPONSE)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("Iron Man")

        # First release has matching media
        assert results[0].metadata["imdb_id"] == "tt0371746"
        assert results[0].metadata["genres"] == "Action, Adventure, Sci-Fi"
        # Second release has no matching media
        assert results[1].metadata["imdb_id"] == ""

    @pytest.mark.asyncio
    async def test_search_empty_query_browses(self, plugin, mock_client):
        resp = _make_json_response(BROWSE_RESPONSE)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("")

        assert len(results) == 2
        assert results[0].title == "Latest Movie (2025)"
        assert results[0].metadata["imdb_id"] == "tt1234567"

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin, mock_client):
        resp = _make_json_response(EMPTY_SEARCH_RESPONSE)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin.search("Iron Man")

        assert results == []

    @pytest.mark.asyncio
    async def test_category_filtering_movies(self, plugin, mock_client):
        # Response with both movie and episode releases
        mixed = {
            "result": {
                "releases": [
                    *SEARCH_RESPONSE["result"]["releases"],
                    *SEARCH_RESPONSE_WITH_EPISODE["result"]["releases"],
                ],
                "media": [],
                "actor": [],
                "director": [],
                "writer": [],
                "author": [],
                "artist": [],
            }
        }
        resp = _make_json_response(mixed)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("test", category=2000)

        assert all(r.category == 2000 for r in results)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_category_filtering_tv(self, plugin, mock_client):
        mixed = {
            "result": {
                "releases": [
                    *SEARCH_RESPONSE["result"]["releases"],
                    *SEARCH_RESPONSE_WITH_EPISODE["result"]["releases"],
                ],
                "media": [],
                "actor": [],
                "director": [],
                "writer": [],
                "author": [],
                "artist": [],
            }
        }
        resp = _make_json_response(mixed)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("test", category=5000)

        assert all(r.category == 5000 for r in results)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_category_rejects_unsupported(self, plugin):
        """Unsupported categories (games=1000, music=3000) return empty."""
        results = await plugin.search("test", category=1000)
        assert results == []

        results = await plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_game_releases_filtered_out(self, plugin, mock_client):
        resp = _make_json_response(SEARCH_RESPONSE_GAME_ONLY)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("game")

        assert results == []

    @pytest.mark.asyncio
    async def test_release_name_is_scene_name(self, plugin, mock_client):
        resp = _make_json_response(SEARCH_RESPONSE)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("Iron Man")

        assert results[0].release_name == "Iron.Man.2008.German.DL.1080p.BluRay.x264-GROUP"

    @pytest.mark.asyncio
    async def test_download_link_is_release_page(self, plugin, mock_client):
        resp = _make_json_response(SEARCH_RESPONSE)
        mock_client.get = AsyncMock(return_value=resp)

        results = await plugin.search("Iron Man")

        assert results[0].download_link.startswith("https://nox.to/release/")

    @pytest.mark.asyncio
    async def test_browse_escalates_days(self, plugin, mock_client):
        """Browse should try escalating days when results are too few."""
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            url_str = str(url)
            if "/latest/1" in url_str:
                return _make_json_response(BROWSE_RESPONSE_SMALL)
            if "/latest/3" in url_str:
                return _make_json_response(BROWSE_RESPONSE_SMALL)
            # Return enough results on day 7
            big_response = {
                "result": [BROWSE_RESPONSE["result"][0]] * 60
            }
            return _make_json_response(big_response)

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("")

        # Should have escalated past day 1 and 3
        assert call_count >= 3


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestNoxCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, nox_mod):
        p = _make_plugin(nox_mod)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, nox_mod):
        p = _make_plugin(nox_mod)

        await p.cleanup()  # Should not raise
