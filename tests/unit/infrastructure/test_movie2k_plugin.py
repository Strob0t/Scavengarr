"""Tests for the movie2k.cx Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "movie2k.py"


def _load_module() -> ModuleType:
    """Load movie2k.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("movie2k_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_Movie2kPlugin = _mod.Movie2kPlugin
_SearchResultParser = _mod._SearchResultParser
_BrowseResultParser = _mod._BrowseResultParser
_DetailPageParser = _mod._DetailPageParser
_detect_category = _mod._detect_category
_domain_from_url = _mod._domain_from_url
_filter_by_category = _mod._filter_by_category


def _make_plugin() -> object:
    """Create Movie2kPlugin instance with domain verification skipped."""
    plug = _Movie2kPlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<h1>Suchergebnisse: "Iron Man" (2 gefunden)</h1>
<table>
  <tr>
    <td><img src="https://image.tmdb.org/t/p/w92/poster1.jpg" alt="Iron Man"></td>
    <td>
      <h2><a href="/stream/iron-man--abc123">Iron Man</a><img alt="Deutsch"></h2>
      <div>2008 | Action, Science Fiction</div>
    </td>
  </tr>
</table>
<table>
  <tr>
    <td><img src="https://image.tmdb.org/t/p/w92/poster2.jpg" alt="Iron Man 3"></td>
    <td>
      <h2><a href="/stream/iron-man-3--def456">Iron Man 3</a><img alt="Deutsch"></h2>
      <div>2013 | Action, Abenteuer</div>
    </td>
  </tr>
</table>
</body></html>
"""

_DETAIL_HTML = """\
<html><body>
<h1>Iron Man <img alt="HD"> Qualität: <img alt="HD-1080p"></h1>
<div>Genre: <a href="/movies/Action">Action</a>, <a href="/movies/Science Fiction">\
Science Fiction</a></div>
<div>IMDB Bewertung: <a href="https://www.imdb.com/title/tt0371746">6.93</a> \
| Aufrufe: 12345 | Genre: Action, Science Fiction | Länge: 126 Minuten \
| Land/Jahr: USA/2008</div>
<div>Tony Stark baut sich in einer Hoehle einen \
Kampfanzug und wird zum Superhelden Iron Man und rettet die Welt.</div>
<div id="tablemoviesindex2">
  <table><tr><td>
    <a href="https://voe.sx/ssbkh7j0ksb6">
      17-12-25 17:28 <img src="flag.png"> voe.sx
      <div>Qualität: <img alt="HD-1080p"></div>
    </a>
  </td></tr></table>
  <table><tr><td>
    <a href="https://vidoza.net/abc123.html">
      17-12-25 17:28 <img src="flag.png"> vidoza.net
      <div>Qualität: <img alt="HD"></div>
    </a>
  </td></tr></table>
  <table><tr><td>
    <a href="https://vinovo.to/xyz789">
      17-12-25 17:28 <img src="flag.png"> vinovo.to
      <div>Qualität: <img alt="HD-1080p"></div>
    </a>
  </td></tr></table>
</div>
</body></html>
"""

_BROWSE_HTML = """\
<html><body>
<h1>Filme (5922)</h1>
<table>
  <tr>
    <td><img src="https://image.tmdb.org/t/p/w92/poster1.jpg"></td>
    <td>
      <h2><a href="/stream/movie-one--aaa111">Movie One</a><img alt="Deutsch"></h2>
      <div>
        Genre: <a href="/movies/Action">Action</a>, <a href="/movies/Drama">Drama</a>
        | Bewertung: 7.5 | 2024 | 120 Min
        <a href="#">Info</a>
      </div>
    </td>
  </tr>
</table>
<table>
  <tr>
    <td><img src="https://image.tmdb.org/t/p/w92/poster2.jpg"></td>
    <td>
      <h2><a href="/stream/movie-two--bbb222">Movie Two</a><img alt="Deutsch"></h2>
      <div>
        Genre: <a href="/movies/Komödie">Komödie</a>
        | Bewertung: 6.0 | 2023 | 95 Min
        <a href="#">Info</a>
      </div>
    </td>
  </tr>
</table>
<a href="/movies?page=2">Nächste »</a>
</body></html>
"""

_EMPTY_SEARCH_HTML = """\
<html><body>
<h1>Suchergebnisse: "xyznonexistent" (0 gefunden)</h1>
</body></html>
"""

_NO_LINKS_DETAIL_HTML = """\
<html><body>
<h1>Some Movie</h1>
<div>Genre: <a href="/movies/Drama">Drama</a></div>
<div>Keine Links verfügbar</div>
</body></html>
"""

_TV_DETAIL_HTML = """\
<html><body>
<h1>Breaking Bad <img alt="HD"></h1>
<div>Genre: <a href="/movies/Drama">Drama</a>, <a href="/movies/Krimi">Krimi</a></div>
<div>IMDB Bewertung: <a href="https://www.imdb.com/title/tt0903747">9.50</a> \
| Aufrufe: 54321 | Länge: 49 Minuten | Land/Jahr: USA/2008</div>
<div id="tablemoviesindex2">
  <table><tr><td>
    <a href="https://voe.sx/breaking1">
      25-01-15 12:00 <img src="flag.png"> voe.sx
      <div>Qualität: <img alt="HD-1080p"></div>
    </a>
  </td></tr></table>
</div>
</body></html>
"""

_ANIME_DETAIL_HTML = """\
<html><body>
<h1>Naruto</h1>
<div>Genre: <a href="/movies/Animation">Animation</a>, \
<a href="/movies/Action">Action</a></div>
<div>IMDB Bewertung: <a href="https://www.imdb.com/title/tt0409591">8.40</a> \
| Land/Jahr: Japan/2002</div>
<div id="tablemoviesindex2">
  <table><tr><td>
    <a href="https://voe.sx/naruto1">
      voe.sx
      <div>Qualität: <img alt="HD"></div>
    </a>
  </td></tr></table>
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
        request=httpx.Request("GET", "https://movie2k.cx/"),
    )


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestDetectCategory:
    """Tests for _detect_category."""

    def test_movie_default(self) -> None:
        assert _detect_category(["Action", "Drama"], is_tv=False) == 2000

    def test_tv_series(self) -> None:
        assert _detect_category(["Drama", "Krimi"], is_tv=True) == 5000

    def test_anime_from_animation(self) -> None:
        assert _detect_category(["Animation", "Action"], is_tv=False) == 5070

    def test_anime_tv(self) -> None:
        assert _detect_category(["Anime", "Action"], is_tv=True) == 5070

    def test_empty_genres_movie(self) -> None:
        assert _detect_category([], is_tv=False) == 2000

    def test_empty_genres_tv(self) -> None:
        assert _detect_category([], is_tv=True) == 5000


class TestDomainFromUrl:
    """Tests for _domain_from_url."""

    def test_extracts_domain(self) -> None:
        assert _domain_from_url("https://voe.sx/ssbkh7j0ksb6") == "voe.sx"

    def test_strips_www(self) -> None:
        assert _domain_from_url("https://www.imdb.com/title/tt123") == "imdb.com"

    def test_invalid_url(self) -> None:
        assert _domain_from_url("not-a-url") == "unknown"


class TestFilterByCategory:
    """Tests for _filter_by_category."""

    def _make_result(self, category: int) -> object:
        from scavengarr.domain.plugins.base import SearchResult

        return SearchResult(
            title="Test",
            download_link="https://example.com",
            category=category,
        )

    def test_filter_movies(self) -> None:
        results = [self._make_result(2000), self._make_result(5000)]
        filtered = _filter_by_category(results, 2000)
        assert len(filtered) == 1
        assert filtered[0].category == 2000

    def test_filter_tv(self) -> None:
        results = [self._make_result(2000), self._make_result(5000)]
        filtered = _filter_by_category(results, 5000)
        assert len(filtered) == 1
        assert filtered[0].category == 5000

    def test_no_filter(self) -> None:
        results = [self._make_result(2000), self._make_result(5000)]
        filtered = _filter_by_category(results, 0)
        assert len(filtered) == 2


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://movie2k.cx")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Iron Man"
        assert first["url"] == "https://movie2k.cx/stream/iron-man--abc123"

        second = parser.results[1]
        assert second["title"] == "Iron Man 3"
        assert second["url"] == "https://movie2k.cx/stream/iron-man-3--def456"

    def test_empty_page(self) -> None:
        parser = _SearchResultParser("https://movie2k.cx")
        parser.feed(_EMPTY_SEARCH_HTML)
        assert len(parser.results) == 0

    def test_deduplicates_same_url(self) -> None:
        html = """\
        <h2><a href="/stream/test--abc">Test</a></h2>
        <h2><a href="/stream/test--abc">Test Again</a></h2>
        """
        parser = _SearchResultParser("https://movie2k.cx")
        parser.feed(html)
        assert len(parser.results) == 1

    def test_skips_non_stream_links(self) -> None:
        html = '<h2><a href="/other/page">Other</a></h2>'
        parser = _SearchResultParser("https://movie2k.cx")
        parser.feed(html)
        assert len(parser.results) == 0


class TestBrowseResultParser:
    """Tests for _BrowseResultParser."""

    def test_parses_browse_results(self) -> None:
        parser = _BrowseResultParser("https://movie2k.cx")
        parser.feed(_BROWSE_HTML)
        parser.finalize()

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Movie One"
        assert first["url"] == "https://movie2k.cx/stream/movie-one--aaa111"
        assert "Action" in first["genres"]
        assert "Drama" in first["genres"]
        assert first["year"] == "2024"
        assert first["rating"] == "7.5"
        assert first["runtime"] == "120"

        second = parser.results[1]
        assert second["title"] == "Movie Two"
        assert "Komödie" in second["genres"]
        assert second["year"] == "2023"

    def test_empty_page(self) -> None:
        parser = _BrowseResultParser("https://movie2k.cx")
        parser.feed("<html><body>No content</body></html>")
        parser.finalize()
        assert len(parser.results) == 0

    def test_deduplicates_same_url(self) -> None:
        html = """\
        <h2><a href="/stream/test--abc">Test</a></h2>
        <div>Genre: <a href="/movies/Action">Action</a> | 2024</div>
        <h2><a href="/stream/test--abc">Test Again</a></h2>
        <div>Genre: <a href="/movies/Drama">Drama</a> | 2024</div>
        """
        parser = _BrowseResultParser("https://movie2k.cx")
        parser.feed(html)
        parser.finalize()
        assert len(parser.results) == 1


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_stream_links(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 3

        first = parser.stream_links[0]
        assert first["hoster"] == "voe.sx"
        assert first["link"] == "https://voe.sx/ssbkh7j0ksb6"
        assert first["quality"] == "HD-1080p"

        second = parser.stream_links[1]
        assert second["hoster"] == "vidoza.net"
        assert second["link"] == "https://vidoza.net/abc123.html"
        assert second["quality"] == "HD"

        third = parser.stream_links[2]
        assert third["hoster"] == "vinovo.to"

    def test_parses_title(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "Iron Man"

    def test_parses_genres(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert "Action" in parser.genres
        assert "Science Fiction" in parser.genres

    def test_parses_imdb(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert parser.imdb_url == "https://www.imdb.com/title/tt0371746"
        assert parser.imdb_rating == "6.93"

    def test_parses_metadata(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert parser.year == "2008"
        assert parser.runtime == "126"
        assert parser.country == "USA"

    def test_parses_description(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_DETAIL_HTML)
        parser.finalize()

        assert "Tony Stark" in parser.description or "Kampfanzug" in parser.description

    def test_empty_detail_no_links(self) -> None:
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(_NO_LINKS_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 0
        assert parser.title == "Some Movie"

    def test_ignores_internal_links(self) -> None:
        html = """\
        <div id="tablemoviesindex2">
          <a href="https://movie2k.cx/some/page">internal</a>
          <a href="https://voe.sx/external">voe.sx
            <div>Qualität: <img alt="HD"></div>
          </a>
        </div>
        """
        parser = _DetailPageParser("https://movie2k.cx")
        parser.feed(html)
        parser.finalize()

        assert len(parser.stream_links) == 1
        assert parser.stream_links[0]["hoster"] == "voe.sx"


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestMovie2kPluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "movie2k"

    def test_plugin_provides(self) -> None:
        plug = _make_plugin()
        assert plug.provides == "stream"

    def test_plugin_default_language(self) -> None:
        plug = _make_plugin()
        assert plug.default_language == "de"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"

    def test_plugin_domains(self) -> None:
        plug = _make_plugin()
        assert "movie2k.cx" in plug._domains


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestMovie2kPluginSearch:
    """Tests for Movie2kPlugin.search with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_DETAIL_HTML),  # Iron Man detail
                _mock_response(_DETAIL_HTML),  # Iron Man 3 detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("Iron Man")

        assert len(results) == 2
        titles = {r.title for r in results}
        assert "Iron Man" in titles

    @pytest.mark.asyncio
    async def test_search_empty_query_browses_movies(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_BROWSE_HTML),  # browse page 1
                _mock_response(""),  # browse page 2 (empty → stop)
                _mock_response(_DETAIL_HTML),  # Movie One detail
                _mock_response(_DETAIL_HTML),  # Movie Two detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("")

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_empty_query_tv_browses_tv(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_BROWSE_HTML),  # TV browse page 1
                _mock_response(""),  # page 2 (empty)
                _mock_response(_TV_DETAIL_HTML),  # TV detail
                _mock_response(_TV_DETAIL_HTML),  # TV detail
            ]
        )

        plug._client = mock_client
        await plug.search("", category=5000)

        # Verify /tv/all was called (not /movies)
        first_call = mock_client.get.call_args_list[0]
        assert "/tv/all" in str(first_call)

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))

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

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                httpx.ConnectError("detail failed"),  # detail page 1
                httpx.ConnectError("detail failed"),  # detail page 2
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_without_streams_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_NO_LINKS_DETAIL_HTML),  # detail without streams
                _mock_response(_NO_LINKS_DETAIL_HTML),  # detail without streams
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_category_filtering_movies(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_DETAIL_HTML),  # movie detail (cat 2000)
                _mock_response(_TV_DETAIL_HTML),  # tv detail (cat 5000)
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=2000)

        # Only movie results (category < 5000)
        assert all(r.category < 5000 for r in results)

    @pytest.mark.asyncio
    async def test_category_filtering_tv(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_DETAIL_HTML),  # movie detail (cat 2000)
                _mock_response(_TV_DETAIL_HTML),  # tv detail (cat 5000)
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=5000)

        # Only TV results (category >= 5000)
        assert all(r.category >= 5000 for r in results)

    @pytest.mark.asyncio
    async def test_season_param_filters_tv(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_DETAIL_HTML),  # movie detail (cat 2000)
                _mock_response(_TV_DETAIL_HTML),  # tv detail (cat 5000)
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", season=1)

        # season param should filter to TV only
        assert all(r.category >= 5000 for r in results)

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        # Single result to check metadata
        search_html = """\
        <html><body>
        <h2><a href="/stream/iron-man--abc123">Iron Man</a></h2>
        </body></html>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page
                _mock_response(_DETAIL_HTML),  # detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("Iron Man")

        assert len(results) == 1
        first = results[0]
        assert first.title == "Iron Man"
        assert first.category == 2000
        assert first.download_link == "https://voe.sx/ssbkh7j0ksb6"
        assert first.download_links is not None
        assert len(first.download_links) == 3
        assert first.metadata["year"] == "2008"
        assert first.metadata["imdb_rating"] == "6.93"
        assert "Action" in first.metadata["genres"]
        assert first.metadata["country"] == "USA"
        assert first.metadata["runtime"] == "126"
        assert first.metadata["imdb_url"] == "https://www.imdb.com/title/tt0371746"

    @pytest.mark.asyncio
    async def test_anime_detection(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <html><body>
        <h2><a href="/stream/naruto--xyz">Naruto</a></h2>
        </body></html>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(_ANIME_DETAIL_HTML),
            ]
        )

        plug._client = mock_client
        results = await plug.search("Naruto")

        assert len(results) == 1
        assert results[0].category == 5070  # Anime category


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestMovie2kCleanup:
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
