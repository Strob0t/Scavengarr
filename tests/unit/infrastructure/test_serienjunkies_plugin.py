"""Tests for the serienjunkies.org Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "serienjunkies.py"


def _load_module() -> ModuleType:
    """Load serienjunkies.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location(
        "serienjunkies_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_SerienjunkiesPlugin = _mod.SerienjunkiesPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_size_to_bytes = _mod._size_to_bytes
_format_size = _mod._format_size
_matches_season = _mod._matches_season
_matches_episode = _mod._matches_episode
_build_description = _mod._build_description
_hoster_display = _mod._hoster_display


def _make_plugin() -> object:
    """Create SerienjunkiesPlugin instance with domain verification skipped."""
    plug = _SerienjunkiesPlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<h1>Suchergebnis</h1>
<table class="table"><tbody>
  <tr><td><a href="/serie/breaking-bad">Breaking Bad</a></td></tr>
  <tr><td><a href="/serie/better-call-saul">Better Call Saul</a></td></tr>
</tbody></table>
</body></html>
"""

_SINGLE_SEARCH_HTML = """\
<html><body>
<h1>Suchergebnis</h1>
<table class="table"><tbody>
  <tr><td><a href="/serie/dark">Dark</a></td></tr>
</tbody></table>
</body></html>
"""

_EMPTY_SEARCH_HTML = """\
<html><body>
<h1>Suchergebnis</h1>
<table class="table"><tbody></tbody></table>
</body></html>
"""

_DETAIL_HTML = """\
<html><body>
<div id="v-release-list"
     data-mediaid="5d877f4ed58b8f3355fe1d7f"
     data-mediatitle="Breaking Bad"
     data-captchasitekey="6LcxeqUUAAAAAB"
     data-requirelogin="false"
     data-releasename="">
</div>
</body></html>
"""

_DETAIL_NO_MEDIAID_HTML = """\
<html><body>
<div id="content">
  <h1>Not Found</h1>
</div>
</body></html>
"""

_RELEASES_JSON: dict = {
    "S2": {
        "items": [
            {
                "_id": "abc123",
                "name": "Breaking.Bad.S02E01.German.DL.720p.BluRay.x264-UTOPiA",
                "source": "BLURAY",
                "encoding": "x264",
                "resolution": "720p",
                "season": 2,
                "episode": 1,
                "group": "UTOPiA",
                "audio": "AC3",
                "language": "GERMAN",
                "sizevalue": 1200,
                "sizeunit": "MB",
                "_media": "5d877f4ed58b8f3355fe1d7f",
                "hoster": ["filer", "ddownload"],
            },
            {
                "_id": "def456",
                "name": "Breaking.Bad.S02E02.German.DL.1080p.BluRay.x264-iNTENTiON",
                "source": "BLURAY",
                "encoding": "x264",
                "resolution": "1080p",
                "season": 2,
                "episode": 2,
                "group": "iNTENTiON",
                "audio": "DTS",
                "language": "GERMAN",
                "sizevalue": 3500,
                "sizeunit": "MB",
                "_media": "5d877f4ed58b8f3355fe1d7f",
                "hoster": ["rapidgator"],
            },
        ],
        "season": 2,
    },
    "SP": {
        "items": [
            {
                "_id": "sp001",
                "name": "Breaking.Bad.S02.German.DL.720p.BluRay.x264-GZCrew",
                "source": "BLURAY",
                "encoding": "x264",
                "resolution": "720p",
                "season": 2,
                "episode": None,
                "group": "GZCrew",
                "audio": "",
                "language": "GERMAN",
                "sizevalue": 15,
                "sizeunit": "GB",
                "_media": "5d877f4ed58b8f3355fe1d7f",
                "hoster": ["filer", "ddownload"],
            },
        ],
        "season": None,
    },
}

_EMPTY_RELEASES_JSON: dict = {}

_RELEASES_NO_HOSTER_JSON: dict = {
    "S1": {
        "items": [
            {
                "_id": "nohoster",
                "name": "Some.Release.S01E01",
                "season": 1,
                "episode": 1,
                "hoster": [],
                "sizevalue": 0,
                "sizeunit": "MB",
            },
        ],
        "season": 1,
    },
}


def _mock_response(
    text: str = "",
    status_code: int = 200,
    json_data: dict | None = None,
) -> httpx.Response:
    """Create a mock httpx.Response."""
    content = text.encode("utf-8")
    if json_data is not None:
        content = json.dumps(json_data).encode("utf-8")
    return httpx.Response(
        status_code=status_code,
        content=content,
        request=httpx.Request("GET", "https://serienjunkies.org/"),
        headers={"content-type": "application/json" if json_data else "text/html"},
    )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestSizeToBytes:
    """Tests for _size_to_bytes."""

    def test_mb(self) -> None:
        assert _size_to_bytes(1200, "MB") == 1200 * 1024**2

    def test_gb(self) -> None:
        assert _size_to_bytes(15, "GB") == 15 * 1024**3

    def test_kb(self) -> None:
        assert _size_to_bytes(500, "KB") == 500 * 1024

    def test_unknown_unit_defaults_mb(self) -> None:
        assert _size_to_bytes(100, "XY") == 100 * 1024**2

    def test_zero(self) -> None:
        assert _size_to_bytes(0, "MB") == 0


class TestFormatSize:
    """Tests for _format_size."""

    def test_integer(self) -> None:
        assert _format_size(1200, "MB") == "1200 MB"

    def test_float(self) -> None:
        assert _format_size(1.5, "GB") == "1.5 GB"

    def test_zero_returns_empty(self) -> None:
        assert _format_size(0, "MB") == ""


class TestMatchesSeason:
    """Tests for _matches_season."""

    def test_no_filter(self) -> None:
        assert _matches_season({"season": 3}, None) is True

    def test_match(self) -> None:
        assert _matches_season({"season": 2}, 2) is True

    def test_no_match(self) -> None:
        assert _matches_season({"season": 3}, 2) is False

    def test_release_no_season(self) -> None:
        assert _matches_season({}, 2) is False


class TestMatchesEpisode:
    """Tests for _matches_episode."""

    def test_no_filter(self) -> None:
        assert _matches_episode({"episode": 5}, None) is True

    def test_match(self) -> None:
        assert _matches_episode({"episode": 3}, 3) is True

    def test_no_match(self) -> None:
        assert _matches_episode({"episode": 4}, 3) is False

    def test_season_pack_matches_any(self) -> None:
        """Season packs (episode=None) match any episode filter."""
        assert _matches_episode({"episode": None}, 3) is True


class TestBuildDescription:
    """Tests for _build_description."""

    def test_full_description(self) -> None:
        release = {
            "resolution": "720p",
            "source": "BLURAY",
            "encoding": "x264",
            "audio": "AC3",
            "language": "GERMAN",
            "group": "UTOPiA",
        }
        desc = _build_description(release)
        assert "720p" in desc
        assert "BLURAY" in desc
        assert "x264" in desc
        assert "AC3" in desc
        assert "GERMAN" in desc
        assert "[UTOPiA]" in desc

    def test_partial_description(self) -> None:
        release = {"resolution": "1080p", "encoding": "h265"}
        desc = _build_description(release)
        assert "1080p" in desc
        assert "h265" in desc

    def test_empty_release(self) -> None:
        assert _build_description({}) == ""


class TestHosterDisplay:
    """Tests for _hoster_display."""

    def test_known_hoster(self) -> None:
        assert _hoster_display("filer") == "Filer.net"
        assert _hoster_display("ddownload") == "DDownload"
        assert _hoster_display("rapidgator") == "RapidGator"

    def test_unknown_hoster(self) -> None:
        assert _hoster_display("somehoster") == "somehoster"


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Breaking Bad"
        assert first["slug"] == "breaking-bad"

        second = parser.results[1]
        assert second["title"] == "Better Call Saul"
        assert second["slug"] == "better-call-saul"

    def test_empty_page(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_EMPTY_SEARCH_HTML)
        assert len(parser.results) == 0

    def test_deduplicates_same_slug(self) -> None:
        html = """\
        <table>
          <tr><td><a href="/serie/dark">Dark</a></td></tr>
          <tr><td><a href="/serie/dark">Dark (duplicate)</a></td></tr>
        </table>
        """
        parser = _SearchResultParser()
        parser.feed(html)
        assert len(parser.results) == 1

    def test_skips_search_link(self) -> None:
        """The search form action link should not be treated as a result."""
        html = '<a href="/serie/search">Search</a>'
        parser = _SearchResultParser()
        parser.feed(html)
        assert len(parser.results) == 0

    def test_skips_non_serie_links(self) -> None:
        html = '<td><a href="/other/page">Other</a></td>'
        parser = _SearchResultParser()
        parser.feed(html)
        assert len(parser.results) == 0


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_extracts_media_id(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.media_id == "5d877f4ed58b8f3355fe1d7f"
        assert parser.media_title == "Breaking Bad"

    def test_no_media_id(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_NO_MEDIAID_HTML)

        assert parser.media_id == ""
        assert parser.media_title == ""

    def test_empty_page(self) -> None:
        parser = _DetailPageParser()
        parser.feed("<html><body></body></html>")
        assert parser.media_id == ""


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestSerienjunkiesPluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "serienjunkies"

    def test_plugin_provides(self) -> None:
        plug = _make_plugin()
        assert plug.provides == "download"

    def test_plugin_default_language(self) -> None:
        plug = _make_plugin()
        assert plug.default_language == "de"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"

    def test_plugin_domains(self) -> None:
        plug = _make_plugin()
        assert "serienjunkies.org" in plug._domains

    def test_plugin_base_url(self) -> None:
        plug = _make_plugin()
        assert plug.base_url == "https://serienjunkies.org"

    def test_plugin_max_results(self) -> None:
        plug = _make_plugin()
        assert plug._max_results == 1000

    def test_module_level_plugin(self) -> None:
        assert hasattr(_mod, "plugin")
        assert _mod.plugin.name == "serienjunkies"


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestSerienjunkiesPluginSearch:
    """Tests for SerienjunkiesPlugin.search with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),  # search
                _mock_response(text=_DETAIL_HTML),  # detail (media ID)
                _mock_response(json_data=_RELEASES_JSON),  # releases API
            ]
        )

        plug._client = mock_client
        results = await plug.search("Dark")

        assert len(results) > 0
        assert all(r.category == 5000 for r in results)

    @pytest.mark.asyncio
    async def test_search_with_season_filter(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("Dark", season=2)

        assert len(results) > 0
        for r in results:
            # All results should be for season 2
            assert r.metadata.get("season") == "2"

    @pytest.mark.asyncio
    async def test_search_with_episode_filter(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("Dark", season=2, episode=1)

        # Should match episode 1 + season pack (episode=None)
        assert len(results) >= 1
        for r in results:
            ep = r.metadata.get("episode")
            assert ep is None or ep == "1"

    @pytest.mark.asyncio
    async def test_search_season_no_match(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("Dark", season=99)

        assert results == []

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        results = await plug.search("")
        assert results == []
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            return_value=_mock_response(text=_EMPTY_SEARCH_HTML)
        )

        plug._client = mock_client
        results = await plug.search("xyznonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection failed"))

        plug._client = mock_client
        results = await plug.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_detail_page_missing_media_id(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),  # search
                _mock_response(text=_DETAIL_NO_MEDIAID_HTML),  # detail without ID
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_releases_api_empty(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_EMPTY_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_releases_without_hoster_skipped(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_NO_HOSTER_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("Breaking Bad")

        # Find the specific episode release
        ep_results = [r for r in results if r.metadata.get("episode") == "1"]
        assert len(ep_results) >= 1

        first = ep_results[0]
        assert "Breaking Bad" in first.title
        expected_name = "Breaking.Bad.S02E01.German.DL.720p.BluRay.x264-UTOPiA"
        assert first.release_name == expected_name
        assert first.category == 5000
        assert first.size is not None
        assert int(first.size) == 1200 * 1024**2
        assert first.metadata["resolution"] == "720p"
        assert first.metadata["source"] == "BLURAY"
        assert first.metadata["encoding"] == "x264"
        assert first.metadata["audio"] == "AC3"
        assert first.metadata["language"] == "GERMAN"
        assert first.metadata["release_group"] == "UTOPiA"
        assert "Filer.net" in first.metadata["hosters"]
        assert "DDownload" in first.metadata["hosters"]
        assert first.source_url == "https://serienjunkies.org/serie/dark"
        assert first.download_links is not None
        assert len(first.download_links) == 2

    @pytest.mark.asyncio
    async def test_result_description(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")

        ep_results = [r for r in results if r.metadata.get("episode") == "1"]
        assert len(ep_results) >= 1
        desc = ep_results[0].description
        assert "720p" in desc
        assert "BLURAY" in desc

    @pytest.mark.asyncio
    async def test_season_pack_included(self) -> None:
        """Season packs (episode=None) should be included."""
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                _mock_response(text=_DETAIL_HTML),
                _mock_response(json_data=_RELEASES_JSON),
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", season=2)

        # Season packs match season=2
        pack_results = [r for r in results if r.metadata.get("episode") is None]
        assert len(pack_results) >= 1
        assert "S02" in pack_results[0].release_name

    @pytest.mark.asyncio
    async def test_multiple_series_scraped(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SEARCH_HTML),  # search (2 results)
                _mock_response(text=_DETAIL_HTML),  # detail 1
                _mock_response(json_data=_RELEASES_JSON),  # releases 1
                _mock_response(text=_DETAIL_HTML),  # detail 2
                _mock_response(json_data=_RELEASES_JSON),  # releases 2
            ]
        )

        plug._client = mock_client
        results = await plug.search("Breaking")

        # Each series has 3 releases -> 6 total
        assert len(results) == 6

    @pytest.mark.asyncio
    async def test_detail_fetch_error_skips_series(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(text=_SINGLE_SEARCH_HTML),
                httpx.ConnectError("detail failed"),
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")
        assert results == []


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestSerienjunkiesCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_called_once()
        assert plug._client is None

    @pytest.mark.asyncio
    async def test_cleanup_noop_without_client(self) -> None:
        plug = _make_plugin()
        plug._client = None

        await plug.cleanup()
        assert plug._client is None
