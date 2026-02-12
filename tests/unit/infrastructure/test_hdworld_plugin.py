"""Unit tests for the hd-world.cc plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "hdworld.py"


@pytest.fixture()
def hdworld_mod():
    """Import hdworld plugin module."""
    spec = importlib.util.spec_from_file_location("hdworld", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hdworld"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("hdworld", None)


# ---------------------------------------------------------------------------
# HTML content fixtures
# ---------------------------------------------------------------------------

_MOVIE_CONTENT = (
    "<p>Ein spannendes Abenteuer im Weltraum.</p>\n"
    "<blockquote><p>Laufzeit: 02:15:00.000<br/>\n"
    "Avg. Bitrate: 12,586 kb/s<br/>\n"
    "Aufloesung: 1920 x 1080<br/>\n"
    "Video Bitrate: 12,168 kb/s @ AVC<br/>\n"
    "Audio #1 Deutsch AC-3 192 kb/s (2 Kan\u00e4le)</p></blockquote>\n"
    '<p><img src="http://hd-world.cc/wp-content/uploads/images/'
    'tt1234567-SHD.jpg" height=436 width=297><br/>\n'
    "<strong>Dauer: </strong>135 Min. | "
    "<strong>Format: </strong>MKV | "
    "<strong>Gr\u00f6\u00dfe:</strong> 10269 MB | "
    '<a href="https://www.imdb.com/title/tt1234567/" '
    'target="_blank" rel="noopener">IMDb: 7.5</a><br/>\n'
    '<strong>Download:</strong> <a href="https://filecrypt.cc/'
    'Container/AAAA11111111.html" target="_blank" rel="noopener">'
    "DDownload.com </a></font><br/>\n"
    '<strong>Mirror #1:</strong> <a href="https://filecrypt.cc/'
    'Container/BBBB22222222.html" target="_blank" rel="noopener">'
    "Rapidgator.net </a></font><br/>\n"
    '<strong>Mirror #2:</strong> <a href="https://filecrypt.cc/'
    'Container/CCCC33333333.html" target="_blank" rel="noopener">'
    "Katfile.com </a></font><br/>\n"
    "<strong>Passwort: </strong>hd-world.cc</p>\n"
)

_SERIES_CONTENT = (
    "<p>Eine dramatische Serienhandlung.</p>\n"
    "<blockquote><p>\n"
    "Show.S01E03.German.DL.720p.WEB.h264-GRP<br/>\n"
    "Show.S01E02.German.DL.720p.WEB.h264-GRP<br/>\n"
    "Show.S01E01.German.DL.720p.WEB.h264-GRP\n"
    "</p></blockquote>\n"
    "<blockquote><p>Avg. Bitrate: 2 909 kb/s<br/>\n"
    "Aufloesung: 1280 x 720</p></blockquote>\n"
    '<p><img src="http://hd-world.cc/wp-content/uploads/images/'
    'tt9999999-SHD.jpg" height=436 width=297><br/>\n'
    "<strong>Dauer: </strong>45 Min. pro Folge | "
    "<strong>Format: </strong>MKV | "
    "<strong>Gr\u00f6\u00dfe:</strong> 3200 MB | "
    '<a href="https://www.imdb.com/title/tt9999999/" '
    'target="_blank" rel="noopener">IMDb: 8.1</a><br/>\n'
    '<strong>Download:</strong> <a href="https://filecrypt.cc/'
    'Container/DDDD44444444.html" target="_blank" rel="noopener">'
    "DDownload.com </a></font><br/>\n"
    "<strong>Passwort: </strong>hd-world.cc</p>\n"
)

_MINIMAL_CONTENT = (
    "<p>Kurze Beschreibung.</p>\n"
    '<p><strong>Download:</strong> <a href="https://filecrypt.cc/'
    'Container/EEEE55555555.html" target="_blank" rel="noopener">'
    "DDownload.com </a></font></p>\n"
)


def _make_post(
    *,
    post_id: int = 100,
    title: str = "Test.Movie.2025.German.DL.1080p.BluRay.x264-GRP",
    content: str = _MOVIE_CONTENT,
    link: str = "http://hd-world.cc/filme/test-movie/",
    categories: list[int] | None = None,
    date: str = "2025-06-15T10:00:00",
) -> dict:
    """Build a WP post fixture."""
    if categories is None:
        categories = [10]
    return {
        "id": post_id,
        "date": date,
        "slug": "test-movie",
        "link": link,
        "title": {"rendered": title},
        "content": {"rendered": content},
        "categories": categories,
    }


def _make_api_response(
    posts: list[dict],
    total_pages: int = 1,
    status_code: int = 200,
) -> httpx.Response:
    """Build a mock httpx.Response for the WP REST API."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = posts
    resp.text = json.dumps(posts)
    resp.headers = {"X-WP-TotalPages": str(total_pages)}

    def raise_for_status() -> None:
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=MagicMock(),
                response=resp,
            )

    resp.raise_for_status = raise_for_status
    return resp


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_basic_tags(self, hdworld_mod: object) -> None:
        assert hdworld_mod._strip_html("<b>Hello</b>") == "Hello"

    def test_entities(self, hdworld_mod: object) -> None:
        assert hdworld_mod._strip_html("&amp; &lt;") == "& <"

    def test_nested_tags(self, hdworld_mod: object) -> None:
        result = hdworld_mod._strip_html("<p><strong>Test</strong></p>")
        assert result == "Test"


class TestExtractDescription:
    def test_extracts_first_paragraph(self, hdworld_mod: object) -> None:
        desc = hdworld_mod._extract_description(_MOVIE_CONTENT)
        assert desc == "Ein spannendes Abenteuer im Weltraum."

    def test_truncates_long_description(self, hdworld_mod: object) -> None:
        long_text = "<p>" + "A" * 400 + "</p><blockquote>X</blockquote>"
        desc = hdworld_mod._extract_description(long_text)
        assert len(desc) == 300
        assert desc.endswith("...")

    def test_no_paragraph(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_description("<div>No p tag</div>") == ""

    def test_empty_content(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_description("") == ""


class TestExtractSize:
    def test_mb_size(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_size(_MOVIE_CONTENT) == "10269 MB"

    def test_series_size(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_size(_SERIES_CONTENT) == "3200 MB"

    def test_no_size(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_size("<p>No size here</p>") is None

    def test_gb_size(self, hdworld_mod: object) -> None:
        html = "<strong>Gr\u00f6\u00dfe:</strong> 4.2 GB"
        assert hdworld_mod._extract_size(html) == "4.2 GB"


class TestExtractDuration:
    def test_movie_duration(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_duration(_MOVIE_CONTENT) == "135"

    def test_series_duration(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_duration(_SERIES_CONTENT) == "45"

    def test_no_duration(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_duration("<p>Nothing</p>") == ""


class TestExtractImdbId:
    def test_extracts_id(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_imdb_id(_MOVIE_CONTENT) == "tt1234567"

    def test_no_imdb(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_imdb_id("<p>No IMDb</p>") == ""


class TestExtractImdbRating:
    def test_extracts_rating(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_imdb_rating(_MOVIE_CONTENT) == "7.5"

    def test_series_rating(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_imdb_rating(_SERIES_CONTENT) == "8.1"

    def test_no_rating(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_imdb_rating("<p>None</p>") == ""


class TestExtractDownloadLinks:
    def test_movie_links(self, hdworld_mod: object) -> None:
        links = hdworld_mod._extract_download_links(_MOVIE_CONTENT)
        assert len(links) == 3
        assert links[0]["hoster"] == "DDownload.com"
        assert "AAAA11111111" in links[0]["link"]
        assert links[1]["hoster"] == "Rapidgator.net"
        assert links[2]["hoster"] == "Katfile.com"

    def test_series_single_link(self, hdworld_mod: object) -> None:
        links = hdworld_mod._extract_download_links(_SERIES_CONTENT)
        assert len(links) == 1
        assert links[0]["hoster"] == "DDownload.com"

    def test_no_links(self, hdworld_mod: object) -> None:
        links = hdworld_mod._extract_download_links("<p>None</p>")
        assert links == []


class TestExtractPoster:
    def test_extracts_poster(self, hdworld_mod: object) -> None:
        poster = hdworld_mod._extract_poster(_MOVIE_CONTENT)
        assert "tt1234567-SHD.jpg" in poster

    def test_no_poster(self, hdworld_mod: object) -> None:
        assert hdworld_mod._extract_poster("<p>No image</p>") == ""


class TestDetermineCategory:
    def test_movie_category(self, hdworld_mod: object) -> None:
        assert hdworld_mod._determine_category([10], "") == 2000

    def test_movie_scene(self, hdworld_mod: object) -> None:
        assert hdworld_mod._determine_category([63253], "") == 2000

    def test_tv_category(self, hdworld_mod: object) -> None:
        assert hdworld_mod._determine_category([13, 15], "") == 5000

    def test_tv_complete(self, hdworld_mod: object) -> None:
        assert hdworld_mod._determine_category([14], "") == 5000

    def test_mixed_prefers_tv(self, hdworld_mod: object) -> None:
        """TV takes precedence when both movie and TV categories present."""
        assert hdworld_mod._determine_category([10, 13], "") == 5000

    def test_fallback_serien_link(self, hdworld_mod: object) -> None:
        link = "http://hd-world.cc/serien/some-series/"
        assert hdworld_mod._determine_category([42993], link) == 5000

    def test_fallback_filme_link(self, hdworld_mod: object) -> None:
        link = "http://hd-world.cc/filme/some-movie/"
        assert hdworld_mod._determine_category([42993], link) == 2000

    def test_unknown_defaults_movie(self, hdworld_mod: object) -> None:
        assert hdworld_mod._determine_category([1], "") == 2000


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestHdWorldPluginAttributes:
    def test_name(self, hdworld_mod: object) -> None:
        assert hdworld_mod.plugin.name == "hdworld"

    def test_provides(self, hdworld_mod: object) -> None:
        assert hdworld_mod.plugin.provides == "download"

    def test_default_language(self, hdworld_mod: object) -> None:
        assert hdworld_mod.plugin.default_language == "de"

    def test_domains(self, hdworld_mod: object) -> None:
        assert hdworld_mod.plugin._domains == ["hd-world.cc"]

    def test_base_url(self, hdworld_mod: object) -> None:
        assert hdworld_mod.plugin.base_url == "https://hd-world.cc"


# ---------------------------------------------------------------------------
# Build result tests
# ---------------------------------------------------------------------------


class TestBuildResult:
    def test_movie_result(self, hdworld_mod: object) -> None:
        post = _make_post()
        plugin = hdworld_mod.HdWorldPlugin()
        result = plugin._build_result(post)

        assert result is not None
        assert result.title == ("Test.Movie.2025.German.DL.1080p.BluRay.x264-GRP")
        assert result.release_name == result.title
        assert result.category == 2000
        assert result.size == "10269 MB"
        assert result.published_date == "2025-06-15"
        assert result.description == ("Ein spannendes Abenteuer im Weltraum.")
        assert result.metadata["imdb_id"] == "tt1234567"
        assert result.metadata["rating"] == "7.5"
        assert result.metadata["runtime"] == "135"
        assert "tt1234567-SHD.jpg" in result.metadata["poster"]

    def test_movie_download_links(self, hdworld_mod: object) -> None:
        post = _make_post()
        plugin = hdworld_mod.HdWorldPlugin()
        result = plugin._build_result(post)

        assert result is not None
        assert result.download_links is not None
        assert len(result.download_links) == 3
        assert "filecrypt.cc" in result.download_link

    def test_series_result(self, hdworld_mod: object) -> None:
        post = _make_post(
            title="Show.S01.German.DL.720p.WEB.h264-GRP",
            content=_SERIES_CONTENT,
            link="http://hd-world.cc/serien/laufend/show-s01/",
            categories=[13, 15],
        )
        plugin = hdworld_mod.HdWorldPlugin()
        result = plugin._build_result(post)

        assert result is not None
        assert result.category == 5000
        assert result.size == "3200 MB"
        assert result.metadata["imdb_id"] == "tt9999999"
        assert result.metadata["rating"] == "8.1"

    def test_minimal_content(self, hdworld_mod: object) -> None:
        post = _make_post(content=_MINIMAL_CONTENT)
        plugin = hdworld_mod.HdWorldPlugin()
        result = plugin._build_result(post)

        assert result is not None
        assert result.size is None
        assert result.metadata["imdb_id"] == ""
        assert result.metadata["rating"] == ""
        assert result.download_links is not None
        assert len(result.download_links) == 1

    def test_empty_title_returns_none(self, hdworld_mod: object) -> None:
        post = _make_post(title="")
        plugin = hdworld_mod.HdWorldPlugin()
        assert plugin._build_result(post) is None

    def test_no_download_link_uses_post_link(self, hdworld_mod: object) -> None:
        post = _make_post(content="<p>No links here</p>")
        plugin = hdworld_mod.HdWorldPlugin()
        result = plugin._build_result(post)

        assert result is not None
        assert result.download_link == "http://hd-world.cc/filme/test-movie/"
        assert result.download_links is None

    def test_source_url_is_post_link(self, hdworld_mod: object) -> None:
        post = _make_post()
        plugin = hdworld_mod.HdWorldPlugin()
        result = plugin._build_result(post)

        assert result is not None
        assert result.source_url == "http://hd-world.cc/filme/test-movie/"


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestHdWorldSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, hdworld_mod: object) -> None:
        posts = [_make_post(post_id=1), _make_post(post_id=2)]
        mock_resp = _make_api_response(posts, total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("Iron Man")

        assert len(results) == 2
        assert all(r.category == 2000 for r in results)
        plugin._client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_empty_query_browses(self, hdworld_mod: object) -> None:
        posts = [_make_post(post_id=1)]
        mock_resp = _make_api_response(posts, total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("")

        assert len(results) == 1
        # Verify no 'search' param was passed
        call_kwargs = plugin._client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert "search" not in params

    @pytest.mark.asyncio
    async def test_search_no_results(self, hdworld_mod: object) -> None:
        mock_resp = _make_api_response([], total_pages=0)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, hdworld_mod: object) -> None:
        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        results = await plugin.search("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_category_filtering_movies(self, hdworld_mod: object) -> None:
        posts = [_make_post(post_id=1)]
        mock_resp = _make_api_response(posts, total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("test", category=2000)

        assert len(results) == 1
        call_kwargs = plugin._client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert "10" in str(params.get("categories", ""))

    @pytest.mark.asyncio
    async def test_category_filtering_tv(self, hdworld_mod: object) -> None:
        post = _make_post(
            title="Show.S01.German.DL.720p.WEB.h264-GRP",
            content=_SERIES_CONTENT,
            link="http://hd-world.cc/serien/show-s01/",
            categories=[13, 15],
        )
        mock_resp = _make_api_response([post], total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("test", category=5000)

        assert len(results) == 1
        assert results[0].category == 5000
        call_kwargs = plugin._client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert "13" in str(params.get("categories", ""))

    @pytest.mark.asyncio
    async def test_category_rejects_unsupported(self, hdworld_mod: object) -> None:
        plugin = hdworld_mod.HdWorldPlugin()
        results = await plugin.search("test", category=4000)
        assert results == []

    @pytest.mark.asyncio
    async def test_pagination(self, hdworld_mod: object) -> None:
        page1_posts = [_make_post(post_id=i) for i in range(1, 4)]
        page2_posts = [_make_post(post_id=i) for i in range(4, 6)]

        resp1 = _make_api_response(page1_posts, total_pages=2)
        resp2 = _make_api_response(page2_posts, total_pages=2)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(side_effect=[resp1, resp2])

        results = await plugin.search("test")

        assert len(results) == 5
        assert plugin._client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_stops_at_total_pages(self, hdworld_mod: object) -> None:
        posts = [_make_post(post_id=1)]
        mock_resp = _make_api_response(posts, total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        await plugin.search("test")

        # Should only call once since total_pages=1
        plugin._client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_result_metadata(self, hdworld_mod: object) -> None:
        posts = [_make_post()]
        mock_resp = _make_api_response(posts, total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("test")

        assert len(results) == 1
        r = results[0]
        assert r.metadata["imdb_id"] == "tt1234567"
        assert r.metadata["rating"] == "7.5"
        assert r.metadata["runtime"] == "135"
        assert "tt1234567-SHD.jpg" in r.metadata["poster"]
        assert r.size == "10269 MB"
        assert r.release_name is not None

    @pytest.mark.asyncio
    async def test_skips_posts_with_empty_title(self, hdworld_mod: object) -> None:
        posts = [
            _make_post(post_id=1, title=""),
            _make_post(post_id=2),
        ]
        mock_resp = _make_api_response(posts, total_pages=1)

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=mock_resp)

        results = await plugin.search("test")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_invalid_json_response(self, hdworld_mod: object) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.side_effect = ValueError("bad json")
        resp.headers = {"X-WP-TotalPages": "1"}
        resp.raise_for_status = MagicMock()
        resp.url = "https://hd-world.cc/wp-json/wp/v2/posts"

        plugin = hdworld_mod.HdWorldPlugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=resp)

        results = await plugin.search("test")
        assert results == []


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestHdWorldCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self, hdworld_mod: object) -> None:
        plugin = hdworld_mod.HdWorldPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_called_once()
        assert plugin._client is None

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self, hdworld_mod: object) -> None:
        plugin = hdworld_mod.HdWorldPlugin()
        await plugin.cleanup()  # should not raise
