"""Tests for the kinoger.com Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "kinoger.py"


def _load_module() -> ModuleType:
    """Load kinoger.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("kinoger_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_KinogerPlugin = _mod.KinogerPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_clean_title = _mod._clean_title
_detect_series = _mod._detect_series
_detect_category = _mod._detect_category
_domain_from_url = _mod._domain_from_url


def _make_plugin() -> object:
    """Create KinogerPlugin instance with domain verification skipped."""
    plug = _KinogerPlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<div class="shortstory-in">
  <div class="shortstory-poster">
    <a href="/stream/12345-batman-begins.html">
      <img src="/poster.jpg" alt="Batman Begins">
    </a>
    <span class="badge">WEBRip</span>
  </div>
  <a class="shortstory-title" href="/stream/12345-batman-begins.html">Batman Begins</a>
  <div class="shortstory-content">
    <ul class="breadcrumbs">
      <li>Stream</li>
      <li>Action</li>
      <li>Abenteuer</li>
    </ul>
  </div>
</div>

<div class="shortstory-in">
  <div class="shortstory-poster">
    <a href="/stream/67890-stranger-things.html">
      <img src="/poster2.jpg" alt="Stranger Things">
    </a>
    <span class="badge">S01-04</span>
  </div>
  <a class="shortstory-title" href="/stream/67890-stranger-things.html">\
Stranger Things</a>
  <div class="shortstory-content">
    <ul class="breadcrumbs">
      <li>Stream</li>
      <li>Drama</li>
      <li>Serie</li>
    </ul>
  </div>
</div>
</body></html>
"""

_DETAIL_HTML = """\
<html><body>
<h1>Batman Begins</h1>
<ul class="breadcrumbs">
  <li>Stream</li>
  <li>Action</li>
  <li>Abenteuer</li>
</ul>
<span class="badge">WEBRip</span>
<span class="imdb">IMDb: 8.2</span>
<div class="full-text">
  Ein junger Bruce Wayne reist nach Osten, um dort Kampftechniken zu erlernen.
</div>
<div class="tabs">
  <input id="tab1" type="radio" name="tab-control" checked>
  <input id="tab2" type="radio" name="tab-control">
  <input id="tab3" type="radio" name="tab-control">
  <label for="tab1" title="Stream HD+">Stream HD+</label>
  <label for="tab2" title="Stream 2">Stream 2</label>
  <label for="tab3" title="Stream 3">Stream 3</label>
  <section id="content1">
    <iframe src="https://fsst.online/embed/abc123"></iframe>
  </section>
  <section id="content2">
    <iframe src="https://kinoger.p2pplay.pro/xyz789"></iframe>
  </section>
  <section id="content3">
    <iframe src="https://stmix.io/embed/def456"></iframe>
  </section>
</div>
</body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body>
<h1>Stranger Things Serie</h1>
<ul class="breadcrumbs">
  <li>Stream</li>
  <li>Drama</li>
  <li>Serie</li>
</ul>
<span class="badge">S01-04</span>
<div class="full-text">
  Nach dem Verschwinden eines Jungen werden uebernatuerliche Ereignisse aufgedeckt.
</div>
<div class="tabs">
  <input id="tab1" type="radio" name="tab-control" checked>
  <label for="tab1" title="Stream HD+">Stream HD+</label>
  <section id="content1">
    <iframe src="https://fsst.online/embed/st001"></iframe>
  </section>
</div>
</body></html>
"""

_EMPTY_DETAIL_HTML = """\
<html><body>
<h1>No Streams</h1>
<div class="tabs">
</div>
</body></html>
"""

_ANIME_SEARCH_HTML = """\
<html><body>
<div class="shortstory-in">
  <div class="shortstory-poster">
    <a href="/stream/11111-one-piece.html">
      <img src="/poster.jpg" alt="One Piece">
    </a>
    <span class="badge">S01-20</span>
  </div>
  <a class="shortstory-title" href="/stream/11111-one-piece.html">One Piece</a>
  <div class="shortstory-content">
    <ul class="breadcrumbs">
      <li>Stream</li>
      <li>Anime</li>
      <li>Action</li>
    </ul>
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
        request=httpx.Request("GET", "https://kinoger.com/"),
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

    def test_detects_from_badge(self) -> None:
        assert _detect_series("S01-04", []) is True

    def test_detects_from_genres(self) -> None:
        assert _detect_series("", ["Drama", "Serie"]) is True

    def test_not_series(self) -> None:
        assert _detect_series("", ["Action", "Drama"]) is False

    def test_detects_complex_badge(self) -> None:
        assert _detect_series("S01E01-02,04-05 von 18", []) is True


class TestDetectCategory:
    """Tests for _detect_category."""

    def test_movie_default(self) -> None:
        assert _detect_category(["Action", "Drama"], is_series=False) == 2000

    def test_series(self) -> None:
        assert _detect_category(["Drama", "Serie"], is_series=True) == 5000

    def test_anime_series(self) -> None:
        assert _detect_category(["Anime", "Action"], is_series=True) == 5070

    def test_anime_movie(self) -> None:
        assert _detect_category(["Anime", "Abenteuer"], is_series=False) == 5070


class TestDomainFromUrl:
    """Tests for _domain_from_url."""

    def test_extracts_domain(self) -> None:
        assert _domain_from_url("https://fsst.online/embed/abc") == "fsst"

    def test_strips_www(self) -> None:
        assert _domain_from_url("https://www.example.com/page") == "example"

    def test_invalid_url(self) -> None:
        assert _domain_from_url("not-a-url") == "unknown"


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://kinoger.com")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Batman Begins"
        assert first["url"] == "https://kinoger.com/stream/12345-batman-begins.html"
        assert first["quality"] == "WEBRip"
        assert first["is_series"] is False
        assert "Action" in first["genres"]
        assert "Abenteuer" in first["genres"]
        assert "Stream" not in first["genres"]

        second = parser.results[1]
        assert second["title"] == "Stranger Things"
        assert second["is_series"] is True
        assert "Serie" in second["genres"]

    def test_empty_page(self) -> None:
        parser = _SearchResultParser("https://kinoger.com")
        parser.feed("<html><body>No results</body></html>")
        assert len(parser.results) == 0

    def test_card_without_title_link_skipped(self) -> None:
        html = """\
        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/99999-test.html"><img></a>
          </div>
        </div>
        """
        parser = _SearchResultParser("https://kinoger.com")
        parser.feed(html)
        # No title link → url from poster, but no title text → skipped
        assert len(parser.results) == 0

    def test_anime_search_result(self) -> None:
        parser = _SearchResultParser("https://kinoger.com")
        parser.feed(_ANIME_SEARCH_HTML)

        assert len(parser.results) == 1
        result = parser.results[0]
        assert result["title"] == "One Piece"
        assert result["is_series"] is True
        assert "Anime" in result["genres"]


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_stream_tabs(self) -> None:
        parser = _DetailPageParser("https://kinoger.com")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 3

        first = parser.stream_links[0]
        assert first["hoster"] == "fsst"
        assert first["link"] == "https://fsst.online/embed/abc123"
        assert first["label"] == "Stream HD+"

        second = parser.stream_links[1]
        assert second["hoster"] == "kinoger"
        assert second["link"] == "https://kinoger.p2pplay.pro/xyz789"
        assert second["label"] == "Stream 2"

        third = parser.stream_links[2]
        assert third["hoster"] == "stmix"
        assert third["link"] == "https://stmix.io/embed/def456"

    def test_parses_metadata(self) -> None:
        parser = _DetailPageParser("https://kinoger.com")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "Batman Begins"
        assert parser.quality == "WEBRip"
        assert parser.imdb_rating == "8.2"
        assert "Action" in parser.genres
        assert "Abenteuer" in parser.genres
        assert "Stream" not in parser.genres
        assert parser.is_series is False

    def test_parses_description(self) -> None:
        parser = _DetailPageParser("https://kinoger.com")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert "Kampftechniken" in parser.description

    def test_series_detail(self) -> None:
        parser = _DetailPageParser("https://kinoger.com")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "Stranger Things"
        assert parser.is_series is True
        assert len(parser.stream_links) == 1

    def test_empty_detail(self) -> None:
        parser = _DetailPageParser("https://kinoger.com")
        parser.feed(_EMPTY_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "No Streams"
        assert len(parser.stream_links) == 0
        assert parser.is_series is False

    def test_imdb_rating_extraction(self) -> None:
        html = '<span class="imdb">IMDb: 7.5</span>'
        parser = _DetailPageParser("https://kinoger.com")
        parser.feed(html)
        assert parser.imdb_rating == "7.5"


# ---------------------------------------------------------------------------
# Plugin integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestKinogerPluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "kinoger"

    def test_plugin_version(self) -> None:
        plug = _make_plugin()
        assert plug.version == "1.0.0"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"

    def test_plugin_provides(self) -> None:
        plug = _make_plugin()
        assert plug.provides == "stream"


class TestKinogerPluginSearch:
    """Tests for KinogerPlugin search with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1
                _mock_response(""),  # search page 2 (empty → stop pagination)
                _mock_response(_DETAIL_HTML),  # Batman detail
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things detail
            ]
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
        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/12345-batman.html"><img></a>
            <span class="badge">WEBRip</span>
          </div>
          <a class="shortstory-title" href="/stream/12345-batman.html">\
Batman Begins</a>
          <div class="shortstory-content">
            <ul class="breadcrumbs">
              <li>Stream</li>
              <li>Action</li>
            </ul>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1
                _mock_response(""),  # search page 2 (empty)
                _mock_response(_DETAIL_HTML),  # detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 1
        first = results[0]
        assert first.title == "Batman Begins"
        assert first.category == 2000
        assert first.download_link.startswith("https://")
        assert first.download_links is not None
        assert len(first.download_links) == 3

    @pytest.mark.asyncio
    async def test_search_series_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/67890-stranger-things.html"><img></a>
            <span class="badge">S01-04</span>
          </div>
          <a class="shortstory-title" \
href="/stream/67890-stranger-things.html">Stranger Things</a>
          <div class="shortstory-content">
            <ul class="breadcrumbs">
              <li>Stream</li>
              <li>Drama</li>
              <li>Serie</li>
            </ul>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1
                _mock_response(""),  # search page 2 (empty)
                _mock_response(_SERIES_DETAIL_HTML),  # detail
            ]
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
        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/12345-test.html"><img></a>
          </div>
          <a class="shortstory-title" href="/stream/12345-test.html">Test</a>
          <div class="shortstory-content">
            <ul class="breadcrumbs"><li>Stream</li><li>Action</li></ul>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1
                _mock_response(""),  # search page 2 (empty)
                httpx.ConnectError("detail failed"),  # detail page error
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_without_streams_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/12345-test.html"><img></a>
          </div>
          <a class="shortstory-title" href="/stream/12345-test.html">Test</a>
          <div class="shortstory-content">
            <ul class="breadcrumbs"><li>Stream</li><li>Action</li></ul>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1
                _mock_response(""),  # search page 2 (empty)
                _mock_response(_EMPTY_DETAIL_HTML),  # detail without streams
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/12345-batman.html"><img></a>
          </div>
          <a class="shortstory-title" href="/stream/12345-batman.html">\
Batman Begins</a>
          <div class="shortstory-content">
            <ul class="breadcrumbs">
              <li>Stream</li>
              <li>Action</li>
            </ul>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(""),
                _mock_response(_DETAIL_HTML),
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        first = results[0]
        assert first.metadata.get("quality") == "WEBRip"
        assert first.metadata.get("imdb_rating") == "8.2"
        assert "Action" in first.metadata.get("genres", "")


class TestKinogerCategoryFiltering:
    """Tests for category filtering."""

    @pytest.mark.asyncio
    async def test_filter_movies_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1
                _mock_response(""),  # search page 2 (empty)
                _mock_response(_DETAIL_HTML),  # Batman (movie)
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things (series)
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=2000)

        # Only movie should remain
        assert len(results) == 1
        assert results[0].title == "Batman Begins"

    @pytest.mark.asyncio
    async def test_filter_series_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1
                _mock_response(""),  # search page 2 (empty)
                _mock_response(_DETAIL_HTML),  # Batman (movie)
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things (series)
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=5000)

        # Only series should remain
        assert len(results) == 1
        assert results[0].title == "Stranger Things"


class TestKinogerDomainFallback:
    """Tests for multi-domain fallback."""

    @pytest.mark.asyncio
    async def test_uses_first_working_domain(self) -> None:
        plug = _KinogerPlugin()
        mock_client = AsyncMock()

        # First domain fails, second succeeds
        mock_client.head = AsyncMock(
            side_effect=[
                httpx.ConnectError("kinoger.com down"),
                _mock_response("", 200),
            ]
        )

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://kinoger.to"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_falls_back_to_first_domain(self) -> None:
        plug = _KinogerPlugin()
        mock_client = AsyncMock()

        # All domains fail
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("all down"))

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://kinoger.com"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_skips_verification_if_done(self) -> None:
        plug = _KinogerPlugin()
        plug._domain_verified = True
        plug.base_url = "https://kinoger.to"

        # Should not call head at all
        mock_client = AsyncMock()
        plug._client = mock_client
        await plug._verify_domain()

        mock_client.head.assert_not_called()
        assert plug.base_url == "https://kinoger.to"


class TestKinogerCleanup:
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

        # Should not raise
        await plug.cleanup()
        assert plug._client is None
