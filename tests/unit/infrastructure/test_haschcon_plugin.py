"""Unit tests for the haschcon.com plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "haschcon.py"


@pytest.fixture()
def haschcon_mod():
    """Import haschcon plugin module."""
    spec = importlib.util.spec_from_file_location("haschcon", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["haschcon"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("haschcon", None)


# ---------------------------------------------------------------------------
# JSON / HTML fixtures
# ---------------------------------------------------------------------------

SEARCH_ENTRY_1 = {
    "id": 12345,
    "date": "2025-06-15T10:30:00",
    "title": {"rendered": "Die Fliege"},
    "slug": "die-fliege",
    "link": "https://haschcon.com/video/die-fliege/",
    "excerpt": {"rendered": "<p>Ein Wissenschaftler verwandelt sich...</p>"},
    "content": {"rendered": "<div>Review text</div>"},
    "featured_media": 999,
    "_embedded": {
        "wp:term": [
            [
                {"id": 247, "name": "Horror und Mystery", "slug": "horror"},
                {"id": 250, "name": "Science Fiction und Fantasy", "slug": "scifi"},
            ],
            [
                {"id": 501, "name": "Jeff Goldblum", "slug": "jeff-goldblum"},
                {"id": 502, "name": "Geena Davis", "slug": "geena-davis"},
            ],
        ],
        "wp:featuredmedia": [
            {
                "source_url": "https://haschcon.com/wp-content/uploads/fliege.jpg",
            },
        ],
    },
}

SEARCH_ENTRY_2 = {
    "id": 67890,
    "date": "2024-03-20T14:00:00",
    "title": {"rendered": "Dracula &#8211; Bram Stoker&#8217;s"},
    "slug": "dracula-bram-stokers",
    "link": "https://haschcon.com/video/dracula-bram-stokers/",
    "excerpt": {"rendered": ""},
    "content": {"rendered": ""},
    "_embedded": {
        "wp:term": [[], []],
        "wp:featuredmedia": [],
    },
}

SEARCH_ENTRY_NO_ID = {
    "date": "2024-01-01T00:00:00",
    "title": {"rendered": "No ID Entry"},
    "slug": "no-id",
    "link": "https://haschcon.com/video/no-id/",
    "excerpt": {"rendered": ""},
    "_embedded": {"wp:term": [[], []], "wp:featuredmedia": []},
}

SEARCH_RESULTS = [SEARCH_ENTRY_1, SEARCH_ENTRY_2]

PLAYER_EMBED_YOUTUBE = """
<!DOCTYPE html>
<html>
<head><title>Player</title></head>
<body>
<iframe src="https://www.youtube.com/embed/A7RnUnGoaMk"
  frameborder="0" allowfullscreen></iframe>
</body>
</html>
"""

PLAYER_EMBED_DAILYMOTION = """
<!DOCTYPE html>
<html>
<head><title>Player</title></head>
<body>
<iframe src="https://www.dailymotion.com/embed/video/x9kk3e0"
  frameborder="0" allowfullscreen></iframe>
</body>
</html>
"""

PLAYER_EMBED_EMPTY = """
<!DOCTYPE html>
<html>
<head><title>Player</title></head>
<body>
<p>No video found.</p>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_json_response(
    data: list | dict,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(data)
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    resp.headers = headers or {}
    return resp


def _make_text_response(
    text: str,
    status_code: int = 200,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, haschcon_mod):
        assert haschcon_mod.plugin.name == "haschcon"

    def test_version(self, haschcon_mod):
        assert haschcon_mod.plugin.version == "1.0.0"

    def test_mode(self, haschcon_mod):
        assert haschcon_mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Build search result tests
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    """Tests for _build_search_result method."""

    def test_basic_fields(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_1, "https://www.youtube.com/watch?v=abc123")

        assert sr.title == "Die Fliege"
        assert sr.download_link == "https://www.youtube.com/watch?v=abc123"
        assert sr.source_url == "https://haschcon.com/video/die-fliege/"
        assert sr.published_date == "2025"
        assert sr.category == 2000

    def test_html_entity_unescaping(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_2, None)

        assert sr.title == "Dracula \u2013 Bram Stoker\u2019s"

    def test_video_url_as_download_link(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(
            SEARCH_ENTRY_1,
            "https://www.dailymotion.com/video/x9kk3e0",
        )

        assert sr.download_link == "https://www.dailymotion.com/video/x9kk3e0"

    def test_no_video_url_fallback(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_1, None)

        assert sr.download_link == "https://haschcon.com/video/die-fliege/"

    def test_categories_in_metadata(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_1, None)

        assert sr.metadata["genres"] == "Horror und Mystery, Science Fiction und Fantasy"

    def test_tags_as_actors(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_1, None)

        assert sr.metadata["actors"] == "Jeff Goldblum, Geena Davis"

    def test_featured_image_as_poster(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_1, None)

        assert sr.metadata["poster"] == "https://haschcon.com/wp-content/uploads/fliege.jpg"

    def test_empty_embedded_terms(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_2, None)

        assert sr.metadata["genres"] == ""
        assert sr.metadata["actors"] == ""
        assert sr.metadata["poster"] == ""

    def test_excerpt_as_description(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_1, None)

        assert sr.description == "Ein Wissenschaftler verwandelt sich..."

    def test_empty_description_is_none(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        sr = p._build_search_result(SEARCH_ENTRY_2, None)

        assert sr.description is None

    def test_long_description_truncated(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        entry = {
            **SEARCH_ENTRY_1,
            "excerpt": {"rendered": f"<p>{'A' * 500}</p>"},
        }
        sr = p._build_search_result(entry, None)

        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_no_date(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        entry = {**SEARCH_ENTRY_1, "date": ""}
        sr = p._build_search_result(entry, None)

        assert sr.published_date is None

    def test_source_url_fallback_to_slug(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        entry = {**SEARCH_ENTRY_1, "link": ""}
        sr = p._build_search_result(entry, None)

        assert sr.source_url == "https://haschcon.com/video/die-fliege/"


# ---------------------------------------------------------------------------
# Player embed extraction tests
# ---------------------------------------------------------------------------


class TestFetchPlayerEmbed:
    """Tests for _fetch_player_embed method."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, haschcon_mod, mock_client):
        p = haschcon_mod.HaschconPlugin()
        p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_youtube_extraction(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            return_value=_make_text_response(PLAYER_EMBED_YOUTUBE),
        )
        url = await plugin._fetch_player_embed(12345)

        assert url == "https://www.youtube.com/watch?v=A7RnUnGoaMk"

    @pytest.mark.asyncio
    async def test_dailymotion_extraction(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            return_value=_make_text_response(PLAYER_EMBED_DAILYMOTION),
        )
        url = await plugin._fetch_player_embed(67890)

        assert url == "https://www.dailymotion.com/video/x9kk3e0"

    @pytest.mark.asyncio
    async def test_no_video_found(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            return_value=_make_text_response(PLAYER_EMBED_EMPTY),
        )
        url = await plugin._fetch_player_embed(99999)

        assert url is None

    @pytest.mark.asyncio
    async def test_http_error(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        url = await plugin._fetch_player_embed(12345)

        assert url is None


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for HaschconPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, haschcon_mod, mock_client):
        p = haschcon_mod.HaschconPlugin()
        p._client = mock_client
        p.base_url = "https://haschcon.com"
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, plugin, mock_client):
        search_resp = _make_json_response(
            SEARCH_RESULTS,
            headers={"X-WP-TotalPages": "1"},
        )

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/wp-json/wp/v2/aiovg_videos" in url_str:
                return search_resp
            if "/player-embed/id/12345/" in url_str:
                return _make_text_response(PLAYER_EMBED_YOUTUBE)
            if "/player-embed/id/67890/" in url_str:
                return _make_text_response(PLAYER_EMBED_DAILYMOTION)
            return _make_json_response([])

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("fliege")

        assert len(results) == 2
        assert results[0].title == "Die Fliege"
        assert results[0].download_link == "https://www.youtube.com/watch?v=A7RnUnGoaMk"
        assert results[1].download_link == "https://www.dailymotion.com/video/x9kk3e0"

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
        search_resp = _make_json_response(
            [SEARCH_ENTRY_1],
            headers={"X-WP-TotalPages": "1"},
        )

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/wp-json/wp/v2/aiovg_videos" in url_str:
                return search_resp
            return _make_text_response(PLAYER_EMBED_YOUTUBE)

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("fliege", category=2000)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin, mock_client):
        search_resp = _make_json_response(
            [],
            headers={"X-WP-TotalPages": "1"},
        )
        mock_client.get = AsyncMock(return_value=search_resp)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_player_failure_uses_source_url(self, plugin, mock_client):
        """When player embed fails, download_link falls back to source URL."""
        search_resp = _make_json_response(
            [SEARCH_ENTRY_1],
            headers={"X-WP-TotalPages": "1"},
        )

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/wp-json/wp/v2/aiovg_videos" in url_str:
                return search_resp
            raise httpx.ConnectError("Connection refused")

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("fliege")

        assert len(results) == 1
        assert results[0].download_link == "https://haschcon.com/video/die-fliege/"

    @pytest.mark.asyncio
    async def test_entry_without_id_skipped(self, plugin, mock_client):
        search_resp = _make_json_response(
            [SEARCH_ENTRY_NO_ID],
            headers={"X-WP-TotalPages": "1"},
        )
        mock_client.get = AsyncMock(return_value=search_resp)

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_pagination_multiple_pages(self, plugin, mock_client):
        """Search fetches multiple pages until X-WP-TotalPages is reached."""
        page1_resp = _make_json_response(
            [SEARCH_ENTRY_1],
            headers={"X-WP-TotalPages": "2"},
        )
        page2_resp = _make_json_response(
            [SEARCH_ENTRY_2],
            headers={"X-WP-TotalPages": "2"},
        )
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            url_str = str(url)
            if "/wp-json/wp/v2/aiovg_videos" in url_str:
                call_count += 1
                if call_count == 1:
                    return page1_resp
                return page2_resp
            # Player embeds
            return _make_text_response(PLAYER_EMBED_YOUTUBE)

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("dracula")

        assert len(results) == 2
        assert call_count == 2


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, haschcon_mod):
        p = haschcon_mod.HaschconPlugin()

        await p.cleanup()  # Should not raise
