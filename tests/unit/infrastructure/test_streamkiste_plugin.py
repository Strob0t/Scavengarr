"""Tests for the streamkiste.taxi Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "streamkiste.py"


def _load_module() -> ModuleType:
    """Load streamkiste.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location(
        "streamkiste_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_StreamkistePlugin = _mod.StreamkistePlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_clean_title = _mod._clean_title
_detect_series = _mod._detect_series
_detect_category = _mod._detect_category
_domain_from_url = _mod._domain_from_url
_extract_onclick_url = _mod._extract_onclick_url
_parse_release_text = _mod._parse_release_text


def _make_plugin() -> object:
    """Create StreamkistePlugin instance with domain verification skipped."""
    plug = _StreamkistePlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<div class="movie-preview res_item">
  <div class="movie-title">
    <a href="/film/12345-batman-begins.html" title="Batman Begins">Batman Begins</a>
  </div>
  <div class="movie-release">2025 - Action Abenteuer kinofilme</div>
  <div class="ico-bar">
    <span class="icon-hd"></span>
  </div>
</div>

<div class="movie-preview res_item">
  <div class="movie-title">
    <a href="/film/67890-stranger-things.html" title="Stranger Things">\
Stranger Things</a>
  </div>
  <div class="movie-release">2024 - Drama Serien</div>
  <div class="ico-bar">
    <span class="icon-hd"></span>
  </div>
</div>
</body></html>
"""

_DETAIL_HTML = """\
<html><body>
<div class="info-right">
  <div class="title"><h1>Batman Begins</h1></div>
  <span class="release">(2025)</span>
  <div class="categories">
    <a href="/action/">Action</a>
    <a href="/abenteuer/">Abenteuer</a>
  </div>
  <p>Ein junger Bruce Wayne reist nach Osten, um dort Kampftechniken zu erlernen.</p>
</div>
<div class="average"><span>7.8</span></div>
<a class="streams" onclick="window.open('https://supervideo.cc/abc123')">
  <span class="streaming">Supervideo</span>
  <mark>1080p</mark>
  <span>1.0GB</span>
</a>
<a class="streams" onclick="window.open('https://dropload.io/xyz789')">
  <span class="streaming">Dropload</span>
  <mark>HD</mark>
  <span>1.1GB</span>
</a>
</body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body>
<div class="info-right">
  <div class="title"><h1>Stranger Things Serie</h1></div>
  <span class="release">(2024)</span>
  <div class="categories">
    <a href="/drama/">Drama</a>
    <a href="/serien/">Serien</a>
  </div>
  <p>Nach dem Verschwinden eines Jungen werden Ereignisse aufgedeckt.</p>
</div>
<div class="average"><span>8.7</span></div>
<a class="streams" onclick="window.open('https://supervideo.cc/st001')">
  <span class="streaming">Supervideo</span>
  <mark>1080p</mark>
  <span>2.5GB</span>
</a>
</body></html>
"""

_EMPTY_DETAIL_HTML = """\
<html><body>
<div class="info-right">
  <div class="title"><h1>No Streams</h1></div>
</div>
</body></html>
"""

_ANIME_SEARCH_HTML = """\
<html><body>
<div class="movie-preview res_item">
  <div class="movie-title">
    <a href="/film/11111-one-piece.html" title="One Piece">One Piece</a>
  </div>
  <div class="movie-release">2023 - Animation Action Serien</div>
  <div class="ico-bar">
    <span class="icon-hd"></span>
  </div>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "https://streamkiste.taxi/"),
    )


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestCleanTitle:
    """Tests for _clean_title."""

    def test_strips_film_suffix(self) -> None:
        assert _clean_title("Batman Film") == "Batman"

    def test_strips_serie_suffix(self) -> None:
        assert _clean_title("Stranger Things Serie") == "Stranger Things"

    def test_strips_trailing_year(self) -> None:
        assert _clean_title("Batman (2005)") == "Batman"

    def test_normal_title_unchanged(self) -> None:
        assert _clean_title("Batman Begins") == "Batman Begins"

    def test_strips_whitespace(self) -> None:
        assert _clean_title("  Batman  ") == "Batman"


class TestDetectSeries:
    """Tests for _detect_series."""

    def test_detects_from_serien_genre(self) -> None:
        assert _detect_series(["Drama", "Serien"]) is True

    def test_detects_from_serie_genre(self) -> None:
        assert _detect_series(["Drama", "Serie"]) is True

    def test_not_series(self) -> None:
        assert _detect_series(["Action", "Drama"]) is False

    def test_empty_genres(self) -> None:
        assert _detect_series([]) is False


class TestDetectCategory:
    """Tests for _detect_category."""

    def test_movie_default(self) -> None:
        assert _detect_category(["Action", "Drama"], is_series=False) == 2000

    def test_series(self) -> None:
        assert _detect_category(["Drama", "Serien"], is_series=True) == 5000

    def test_anime_series(self) -> None:
        assert _detect_category(["Animation", "Action"], is_series=True) == 5070

    def test_anime_movie(self) -> None:
        assert _detect_category(["Animation", "Abenteuer"], is_series=False) == 5070

    def test_anime_keyword(self) -> None:
        assert _detect_category(["Anime", "Action"], is_series=False) == 5070


class TestDomainFromUrl:
    """Tests for _domain_from_url."""

    def test_extracts_domain(self) -> None:
        assert _domain_from_url("https://supervideo.cc/abc123") == "supervideo"

    def test_strips_www(self) -> None:
        assert _domain_from_url("https://www.example.com/page") == "example"

    def test_invalid_url(self) -> None:
        assert _domain_from_url("not-a-url") == "unknown"


class TestExtractOnclickUrl:
    """Tests for _extract_onclick_url."""

    def test_single_quotes(self) -> None:
        assert (
            _extract_onclick_url("window.open('https://example.com/abc')")
            == "https://example.com/abc"
        )

    def test_double_quotes(self) -> None:
        assert (
            _extract_onclick_url('window.open("https://example.com/xyz")')
            == "https://example.com/xyz"
        )

    def test_no_match(self) -> None:
        assert _extract_onclick_url("someOtherFunction()") == ""

    def test_empty_string(self) -> None:
        assert _extract_onclick_url("") == ""


class TestParseReleaseText:
    """Tests for _parse_release_text."""

    def test_year_and_genres(self) -> None:
        year, genres = _parse_release_text("2025 - Action Komödie Krimi kinofilme")
        assert year == "2025"
        assert "Action" in genres
        assert "Komödie" in genres
        assert "Krimi" in genres
        assert "kinofilme" not in genres

    def test_year_only(self) -> None:
        year, genres = _parse_release_text("2025")
        assert year == "2025"
        assert genres == []

    def test_genres_only(self) -> None:
        year, genres = _parse_release_text("Action Drama")
        assert year == ""
        assert genres == ["Action", "Drama"]

    def test_empty_text(self) -> None:
        year, genres = _parse_release_text("")
        assert year == ""
        assert genres == []

    def test_year_no_dash(self) -> None:
        year, genres = _parse_release_text("2025 Action")
        assert year == "2025"
        assert genres == ["Action"]


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://streamkiste.taxi")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Batman Begins"
        assert first["url"] == "https://streamkiste.taxi/film/12345-batman-begins.html"
        assert first["year"] == "2025"
        assert "Action" in first["genres"]
        assert "Abenteuer" in first["genres"]
        assert first["quality"] == "HD"
        assert first["is_series"] is False

        second = parser.results[1]
        assert second["title"] == "Stranger Things"
        assert second["year"] == "2024"
        assert second["is_series"] is True

    def test_empty_page(self) -> None:
        parser = _SearchResultParser("https://streamkiste.taxi")
        parser.feed("<html><body>No results</body></html>")
        assert len(parser.results) == 0

    def test_card_without_title_link_skipped(self) -> None:
        html = """\
        <div class="movie-preview res_item">
          <div class="movie-release">2025 - Action</div>
        </div>
        """
        parser = _SearchResultParser("https://streamkiste.taxi")
        parser.feed(html)
        assert len(parser.results) == 0

    def test_anime_search_result(self) -> None:
        parser = _SearchResultParser("https://streamkiste.taxi")
        parser.feed(_ANIME_SEARCH_HTML)

        assert len(parser.results) == 1
        result = parser.results[0]
        assert result["title"] == "One Piece"
        assert result["is_series"] is True
        assert "Animation" in result["genres"]

    def test_title_from_text_fallback(self) -> None:
        """Title from link text when title attr is missing."""
        html = """\
        <div class="movie-preview res_item">
          <div class="movie-title">
            <a href="/film/99999-test.html">Test Movie</a>
          </div>
          <div class="movie-release">2025 - Action</div>
        </div>
        """
        parser = _SearchResultParser("https://streamkiste.taxi")
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Test Movie"


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_stream_links(self) -> None:
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 2

        first = parser.stream_links[0]
        assert first["hoster"] == "Supervideo"
        assert first["link"] == "https://supervideo.cc/abc123"
        assert first["quality"] == "1080p"
        assert first["size"] == "1.0GB"

        second = parser.stream_links[1]
        assert second["hoster"] == "Dropload"
        assert second["link"] == "https://dropload.io/xyz789"
        assert second["quality"] == "HD"
        assert second["size"] == "1.1GB"

    def test_parses_metadata(self) -> None:
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "Batman Begins"
        assert parser.year == "2025"
        assert parser.imdb_rating == "7.8"
        assert "Action" in parser.genres
        assert "Abenteuer" in parser.genres
        assert parser.is_series is False

    def test_parses_description(self) -> None:
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert "Kampftechniken" in parser.description

    def test_series_detail(self) -> None:
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "Stranger Things"
        assert parser.is_series is True
        assert parser.year == "2024"
        assert parser.imdb_rating == "8.7"
        assert len(parser.stream_links) == 1

    def test_empty_detail(self) -> None:
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(_EMPTY_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "No Streams"
        assert len(parser.stream_links) == 0
        assert parser.is_series is False

    def test_imdb_rating_extraction(self) -> None:
        html = '<div class="average"><span>8.5</span></div>'
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(html)
        assert parser.imdb_rating == "8.5"

    def test_stream_without_onclick_skipped(self) -> None:
        html = '<a class="streams" href="#">No onclick</a>'
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(html)
        assert len(parser.stream_links) == 0

    def test_hoster_falls_back_to_domain(self) -> None:
        html = """\
        <a class="streams" onclick="window.open('https://newhost.io/vid123')">
          <mark>720p</mark>
        </a>
        """
        parser = _DetailPageParser("https://streamkiste.taxi")
        parser.feed(html)

        assert len(parser.stream_links) == 1
        assert parser.stream_links[0]["hoster"] == "newhost"


# ---------------------------------------------------------------------------
# Plugin integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestStreamkistePluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "streamkiste"

    def test_plugin_version(self) -> None:
        plug = _make_plugin()
        assert plug.version == "1.0.0"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"


class TestStreamkistePluginSearch:
    """Tests for StreamkistePlugin search with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
                _mock_response(_DETAIL_HTML),  # Batman detail (GET)
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things detail (GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 2
        titles = {r.title for r in results}
        assert "Batman Begins" in titles
        assert "Stranger Things" in titles

    @pytest.mark.asyncio
    async def test_search_movie_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="movie-preview res_item">
          <div class="movie-title">
            <a href="/film/12345-batman.html" title="Batman Begins">\
Batman Begins</a>
          </div>
          <div class="movie-release">2025 - Action kinofilme</div>
          <div class="ico-bar"><span class="icon-hd"></span></div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_DETAIL_HTML),  # detail (GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 1
        first = results[0]
        assert first.title == "Batman Begins"
        assert first.category == 2000
        assert first.download_link.startswith("https://")
        assert first.download_links is not None
        assert len(first.download_links) == 2

    @pytest.mark.asyncio
    async def test_search_series_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="movie-preview res_item">
          <div class="movie-title">
            <a href="/film/67890-stranger-things.html" \
title="Stranger Things">Stranger Things</a>
          </div>
          <div class="movie-release">2024 - Drama Serien</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_SERIES_DETAIL_HTML),  # detail (GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("Stranger Things")

        assert len(results) == 1
        first = results[0]
        assert first.title == "Stranger Things"
        assert first.category == 5000

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        results = await plug.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            return_value=_mock_response("<html><body>No results</body></html>")
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
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="movie-preview res_item">
          <div class="movie-title">
            <a href="/film/12345-test.html" title="Test">Test</a>
          </div>
          <div class="movie-release">2025 - Action</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                httpx.ConnectError("detail failed"),  # detail page error (GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_without_streams_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="movie-preview res_item">
          <div class="movie-title">
            <a href="/film/12345-test.html" title="Test">Test</a>
          </div>
          <div class="movie-release">2025 - Action</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_EMPTY_DETAIL_HTML),  # detail without streams (GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="movie-preview res_item">
          <div class="movie-title">
            <a href="/film/12345-batman.html" title="Batman Begins">\
Batman Begins</a>
          </div>
          <div class="movie-release">2025 - Action</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_DETAIL_HTML),  # detail (GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        first = results[0]
        assert first.metadata.get("quality") == "1080p"
        assert first.metadata.get("imdb_rating") == "7.8"
        assert "Action" in first.metadata.get("genres", "")

    @pytest.mark.asyncio
    async def test_pagination_uses_post_for_page_2(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        # Page 1 returns results via GET, page 2 via POST
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
            ]
        )
        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(""),  # search page 2 (POST, empty → stop)
            ]
        )

        plug._client = mock_client
        # Use _search_all_pages to trigger pagination
        results = await plug._search_all_pages("test")

        assert len(results) == 2
        mock_client.get.assert_called_once()
        mock_client.post.assert_called_once()


class TestStreamkisteCategoryFiltering:
    """Tests for category filtering."""

    @pytest.mark.asyncio
    async def test_filter_movies_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
                _mock_response(_DETAIL_HTML),  # Batman (movie, GET)
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things (series, GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("test", category=2000)

        assert len(results) == 1
        assert results[0].title == "Batman Begins"

    @pytest.mark.asyncio
    async def test_filter_series_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
                _mock_response(_DETAIL_HTML),  # Batman (movie, GET)
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things (series, GET)
            ]
        )
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),  # search page 2 (POST, empty)
        )

        plug._client = mock_client
        results = await plug.search("test", category=5000)

        assert len(results) == 1
        assert results[0].title == "Stranger Things"


class TestStreamkisteDomainFallback:
    """Tests for multi-domain fallback."""

    @pytest.mark.asyncio
    async def test_uses_first_working_domain(self) -> None:
        plug = _StreamkistePlugin()
        mock_client = AsyncMock()

        # First two domains fail, third succeeds
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.url = httpx.URL("https://streamkiste.sx/")
        mock_client.head = AsyncMock(
            side_effect=[
                httpx.ConnectError("streamkiste.taxi down"),
                httpx.ConnectError("streamkiste.tv down"),
                ok_resp,
            ]
        )

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://streamkiste.sx"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_falls_back_to_first_domain(self) -> None:
        plug = _StreamkistePlugin()
        mock_client = AsyncMock()

        # All domains fail
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("all down"))

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://streamkiste.taxi"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_skips_verification_if_done(self) -> None:
        plug = _StreamkistePlugin()
        plug._domain_verified = True
        plug.base_url = "https://streamkiste.tv"

        mock_client = AsyncMock()
        plug._client = mock_client
        await plug._verify_domain()

        mock_client.head.assert_not_called()
        assert plug.base_url == "https://streamkiste.tv"


class TestStreamkisteCleanup:
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
