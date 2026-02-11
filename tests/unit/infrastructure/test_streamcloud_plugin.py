"""Tests for the streamcloud.plus Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "streamcloud.py"


def _load_module() -> ModuleType:
    """Load streamcloud.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location(
        "streamcloud_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_StreamcloudPlugin = _mod.StreamcloudPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_clean_title = _mod._clean_title
_detect_series = _mod._detect_series
_detect_category = _mod._detect_category
_domain_from_url = _mod._domain_from_url
_filter_by_category = _mod._filter_by_category


def _make_plugin() -> object:
    """Create StreamcloudPlugin instance with domain verification skipped."""
    plug = _StreamcloudPlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<div id="dle-content">
<div class="search_result_num grey">Found 2 responses (Query results 1 - 2) :</div>

<div class="item cf item-video post-1515412 post type-post" style="position:relative;">
  <div class="thumb" title="Justice League">
    <a href="https://streamcloud.plus/7244-justice-league-stream-deutsch.html">\
<img src="/uploads/thumb/poster.jpg" alt="Justice League"><span class="overlay">\
</span></a>
  </div>
  <div class="f_title">\
<a href="https://streamcloud.plus/7244-justice-league-stream-deutsch.html">\
Justice League</a></div>
  <div class="f_year">2017</div>
</div>

<div class="item cf item-video post-1515412 post type-post" style="position:relative;">
  <div class="thumb" title="The Batman">
    <a href="https://streamcloud.plus/39187-the-batman-stream-deutsch.html">\
<img src="/uploads/thumb/poster2.jpg" alt="The Batman"><span class="overlay">\
</span></a>
  </div>
  <div class="f_title">\
<a href="https://streamcloud.plus/39187-the-batman-stream-deutsch.html">\
The Batman</a></div>
  <div class="f_year">2004</div>
</div>
</div>
</body></html>
"""

_MOVIE_DETAIL_HTML = """\
<html><body><main>
<script src="https://meinecloud.click/ddl/tt0974015"></script>
<div class="dark"><div id="streams">\
<a id="stream_yes_access" style="cursor:pointer" \
onclick="window.open( 'https://supervideo.cc/hvl13tlrh31o' )" \
target="_blank" rel="noreferrer" class="streams">\
<div><span class="streaming">Supervideo</span>\
<span class="quality"><mark>1080p</mark></span>\
<span style="width:auto; float:right; padding:10px;">\
<span style="color:#999;">1.1GB</span></span></div></a>\
<a id="stream_yes_access" style="cursor:pointer" \
onclick="window.open( 'https://dropload.tv/g70obtkey4gw' )" \
target="_blank" rel="noreferrer" class="streams">\
<div><span class="streaming">Dropload</span>\
<span class="quality"><mark>1080p</mark></span>\
<span style="width:auto; float:right; padding:10px;">\
<span style="color:#999;">1.0GB</span></span></div></a>\
</div></div>
<p>Bruce Wayne alias Batman hat wieder Vertrauen in die Menschheit.</p>
<strong>Genres:</strong> <span>Action / Abenteuer / Sci-Fi / Fantasy</span>
<strong>Veröffentlicht:</strong> <a href="/xfsearch/2017">2017</a>
<strong>Spielzeit:</strong> <span>121 min</span>
<a href="https://www.imdb.com/title/tt0974015/">6.1/10</a>
</main></body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body><main>
<div class="tab-pane fade active show" id="season-1">
  <ul>
    <li class="active">
      <a href="#" data-link="https://supervideo.cc/embed-a7jl6afezqxu.html" \
id="serie-1_1" data-num="1x1" data-title="Episode 1">1</a>
      <div class="mirrors">
        <a href="#" data-m="supervideo" \
data-link="https://supervideo.cc/embed-a7jl6afezqxu.html">Supervideo</a>
        <a href="#" data-m="streamtape" \
data-link="/player/player.php?id=39187&amp;s=1">Streamtape</a>
      </div>
    </li>
    <li>
      <a href="#" data-link="https://supervideo.cc/embed-76jhsp47ukjk.html" \
id="serie-1_2" data-num="1x2" data-title="Episode 2">2</a>
      <div class="mirrors">
        <a href="#" data-m="supervideo" \
data-link="https://supervideo.cc/embed-76jhsp47ukjk.html">Supervideo</a>
      </div>
    </li>
  </ul>
</div>
<p>Joker hat ein Gas entwickelt, mit dem er ganz Gotham City beherrschen kann.</p>
<strong>Genres:</strong> <span>Serien / Animation / Action</span>
<strong>Veröffentlicht:</strong> <a href="/xfsearch/2004">2004</a>
<strong>Staffel:</strong> <span>2</span>
<strong>Episode:</strong> <span>13</span>
<strong>Spielzeit:</strong> <span>20 min</span>
<a href="https://www.imdb.com/title/tt0398417/">7.4/10</a>
</main></body></html>
"""

_EMPTY_DETAIL_HTML = """\
<html><body><main>
<h1>No Streams Available</h1>
<p>Short text.</p>
</main></body></html>
"""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "https://streamcloud.plus/"),
    )


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestCleanTitle:
    """Tests for _clean_title."""

    def test_strips_film_suffix(self) -> None:
        assert _clean_title("Batman Film") == "Batman"

    def test_strips_serie_suffix(self) -> None:
        assert _clean_title("The Batman Serie") == "The Batman"

    def test_strips_trailing_year(self) -> None:
        assert _clean_title("Justice League (2017)") == "Justice League"

    def test_normal_title_unchanged(self) -> None:
        assert _clean_title("Justice League") == "Justice League"

    def test_strips_whitespace(self) -> None:
        assert _clean_title("  Batman  ") == "Batman"


class TestDetectSeries:
    """Tests for _detect_series."""

    def test_detects_from_serien_genre(self) -> None:
        assert _detect_series(["Drama", "Serien"]) is True

    def test_detects_from_serie_genre(self) -> None:
        assert _detect_series(["Action", "Serie"]) is True

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
        assert _detect_category(["Anime", "Action"], is_series=True) == 5070

    def test_anime_movie(self) -> None:
        assert _detect_category(["Animation", "Abenteuer"], is_series=False) == 5070

    def test_animation_series(self) -> None:
        assert _detect_category(["Animation", "Action"], is_series=True) == 5070


class TestDomainFromUrl:
    """Tests for _domain_from_url."""

    def test_extracts_domain(self) -> None:
        assert _domain_from_url("https://supervideo.cc/embed-abc123") == "supervideo"

    def test_strips_www(self) -> None:
        assert _domain_from_url("https://www.example.com/page") == "example"

    def test_invalid_url(self) -> None:
        assert _domain_from_url("not-a-url") == "unknown"

    def test_dropload(self) -> None:
        assert _domain_from_url("https://dropload.tv/g70obtkey4gw") == "dropload"


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://streamcloud.plus")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Justice League"
        assert (
            first["url"]
            == "https://streamcloud.plus/7244-justice-league-stream-deutsch.html"
        )
        assert first["year"] == "2017"

        second = parser.results[1]
        assert second["title"] == "The Batman"
        assert second["year"] == "2004"

    def test_empty_page(self) -> None:
        parser = _SearchResultParser("https://streamcloud.plus")
        parser.feed("<html><body>No results</body></html>")
        assert len(parser.results) == 0

    def test_card_without_title_skipped(self) -> None:
        html = """\
        <div class="item cf item-video">
          <div class="thumb" title="">
            <a href="/12345-test.html"><img></a>
          </div>
          <div class="f_title"><a href="/12345-test.html"></a></div>
          <div class="f_year">2024</div>
        </div>
        """
        parser = _SearchResultParser("https://streamcloud.plus")
        parser.feed(html)
        # Empty title → skipped
        assert len(parser.results) == 0

    def test_uses_thumb_title_as_fallback(self) -> None:
        html = """\
        <div class="item cf item-video">
          <div class="thumb" title="Fallback Title">
            <a href="/12345-test.html"><img></a>
          </div>
          <div class="f_year">2024</div>
        </div>
        """
        parser = _SearchResultParser("https://streamcloud.plus")
        parser.feed(html)
        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Fallback Title"


class TestDetailPageParserMovie:
    """Tests for _DetailPageParser with movie detail pages."""

    def test_parses_movie_hosters(self) -> None:
        parser = _DetailPageParser("https://streamcloud.plus")
        parser.feed(_MOVIE_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 2

        first = parser.stream_links[0]
        assert first["link"] == "https://supervideo.cc/hvl13tlrh31o"
        assert first["hoster"] == "supervideo"
        assert first["quality"] == "1080p"

        second = parser.stream_links[1]
        assert second["link"] == "https://dropload.tv/g70obtkey4gw"
        assert second["hoster"] == "dropload"

    def test_parses_movie_metadata(self) -> None:
        parser = _DetailPageParser("https://streamcloud.plus")
        parser.feed(_MOVIE_DETAIL_HTML)
        parser.finalize()

        assert "Action" in parser.genres
        assert "Abenteuer" in parser.genres
        assert "Sci-Fi" in parser.genres
        assert "Fantasy" in parser.genres
        assert parser.year == "2017"
        assert parser.imdb_rating == "6.1"
        assert parser.imdb_id == "tt0974015"
        assert parser.runtime == "121 min"
        assert parser.is_series is False

    def test_parses_movie_description(self) -> None:
        parser = _DetailPageParser("https://streamcloud.plus")
        parser.feed(_MOVIE_DETAIL_HTML)
        parser.finalize()

        assert "Bruce Wayne" in parser.description


class TestDetailPageParserSeries:
    """Tests for _DetailPageParser with series detail pages."""

    def test_parses_series_episode_links(self) -> None:
        parser = _DetailPageParser("https://streamcloud.plus")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        assert parser.is_series is True
        assert len(parser.stream_links) > 0

        # Should have episode primary links + mirrors
        links = parser.stream_links
        ep1_links = [sl for sl in links if "1x1" in sl.get("label", "")]
        assert len(ep1_links) >= 1

    def test_series_metadata(self) -> None:
        parser = _DetailPageParser("https://streamcloud.plus")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        assert parser.year == "2004"
        assert parser.imdb_rating == "7.4"
        assert parser.imdb_id == "tt0398417"
        assert "Serien" in parser.genres
        assert "Animation" in parser.genres
        assert parser.is_series is True

    def test_empty_detail(self) -> None:
        parser = _DetailPageParser("https://streamcloud.plus")
        parser.feed(_EMPTY_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 0
        assert parser.is_series is False


# ---------------------------------------------------------------------------
# Plugin integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestStreamcloudPluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "streamcloud"

    def test_plugin_version(self) -> None:
        plug = _make_plugin()
        assert plug.version == "1.0.0"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"


class TestStreamcloudPluginSearch:
    """Tests for StreamcloudPlugin search with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        # Page 1 is GET, detail pages are GET
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
                _mock_response(_MOVIE_DETAIL_HTML),  # Justice League detail
                _mock_response(_SERIES_DETAIL_HTML),  # The Batman detail
            ]
        )
        # Page 2+ is POST → empty to stop pagination
        mock_client.post = AsyncMock(
            return_value=_mock_response(""),
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 2
        titles = {r.title for r in results}
        assert "Justice League" in titles
        assert "The Batman" in titles

    @pytest.mark.asyncio
    async def test_search_movie_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="item cf item-video">
          <div class="thumb" title="Justice League">
            <a href="https://streamcloud.plus/7244-justice-league.html"><img></a>
          </div>
          <div class="f_title">\
<a href="https://streamcloud.plus/7244-justice-league.html">\
Justice League</a></div>
          <div class="f_year">2017</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_MOVIE_DETAIL_HTML),  # detail (GET)
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("Justice League")

        assert len(results) == 1
        first = results[0]
        assert first.title == "Justice League"
        assert first.category == 2000
        assert first.download_link.startswith("https://")
        assert first.download_links is not None
        assert len(first.download_links) == 2

    @pytest.mark.asyncio
    async def test_search_series_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="item cf item-video">
          <div class="thumb" title="The Batman">
            <a href="https://streamcloud.plus/39187-the-batman.html"><img></a>
          </div>
          <div class="f_title">\
<a href="https://streamcloud.plus/39187-the-batman.html">\
The Batman</a></div>
          <div class="f_year">2004</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_SERIES_DETAIL_HTML),  # detail (GET)
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("The Batman")

        assert len(results) == 1
        first = results[0]
        assert first.title == "The Batman"
        assert first.category == 5070  # Animation series → anime

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
        <div class="item cf item-video">
          <div class="thumb" title="Test">
            <a href="https://streamcloud.plus/12345-test.html"><img></a>
          </div>
          <div class="f_title">\
<a href="https://streamcloud.plus/12345-test.html">Test</a></div>
          <div class="f_year">2024</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                httpx.ConnectError("detail failed"),  # detail page error
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_without_streams_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="item cf item-video">
          <div class="thumb" title="Test">
            <a href="https://streamcloud.plus/12345-test.html"><img></a>
          </div>
          <div class="f_title">\
<a href="https://streamcloud.plus/12345-test.html">Test</a></div>
          <div class="f_year">2024</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_EMPTY_DETAIL_HTML),  # detail without streams
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="item cf item-video">
          <div class="thumb" title="Justice League">
            <a href="https://streamcloud.plus/7244-jl.html"><img></a>
          </div>
          <div class="f_title">\
<a href="https://streamcloud.plus/7244-jl.html">\
Justice League</a></div>
          <div class="f_year">2017</div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page 1 (GET)
                _mock_response(_MOVIE_DETAIL_HTML),  # detail (GET)
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("Justice League")

        first = results[0]
        assert first.metadata.get("quality") == "1080p"
        assert first.metadata.get("imdb_rating") == "6.1"
        assert first.metadata.get("imdb_id") == "tt0974015"
        assert "Action" in first.metadata.get("genres", "")
        assert first.metadata.get("year") == "2017"
        assert first.metadata.get("runtime") == "121 min"


class TestStreamcloudCategoryFiltering:
    """Tests for category filtering."""

    @pytest.mark.asyncio
    async def test_filter_movies_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
                _mock_response(_MOVIE_DETAIL_HTML),  # Justice League (movie)
                _mock_response(_SERIES_DETAIL_HTML),  # The Batman (series)
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("test", category=2000)

        # Only movie should remain
        assert len(results) == 1
        assert results[0].title == "Justice League"

    @pytest.mark.asyncio
    async def test_filter_series_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1 (GET)
                _mock_response(_MOVIE_DETAIL_HTML),  # Justice League (movie)
                _mock_response(_SERIES_DETAIL_HTML),  # The Batman (series)
            ]
        )
        mock_client.post = AsyncMock(return_value=_mock_response(""))

        plug._client = mock_client
        results = await plug.search("test", category=5000)

        # Only series should remain
        assert len(results) == 1
        assert results[0].title == "The Batman"


class TestStreamcloudDomainFallback:
    """Tests for multi-domain fallback."""

    @pytest.mark.asyncio
    async def test_uses_first_working_domain(self) -> None:
        plug = _StreamcloudPlugin()
        mock_client = AsyncMock()

        # First domain fails, second succeeds
        mock_client.head = AsyncMock(
            side_effect=[
                httpx.ConnectError("streamcloud.plus down"),
                _mock_response("", 200),
            ]
        )

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://streamcloud.my"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_falls_back_to_first_domain(self) -> None:
        plug = _StreamcloudPlugin()
        mock_client = AsyncMock()

        # All domains fail
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("all down"))

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://streamcloud.plus"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_skips_verification_if_done(self) -> None:
        plug = _StreamcloudPlugin()
        plug._domain_verified = True
        plug.base_url = "https://streamcloud.my"

        mock_client = AsyncMock()
        plug._client = mock_client
        await plug._verify_domain()

        mock_client.head.assert_not_called()
        assert plug.base_url == "https://streamcloud.my"


class TestStreamcloudCleanup:
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
