"""Tests for the hdfilme.legal Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "hdfilme.py"


def _load_module() -> ModuleType:
    """Load hdfilme.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("hdfilme_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_HdfilmePlugin = _mod.HdfilmePlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_parse_meinecloud_script = _mod._parse_meinecloud_script


def _make_plugin() -> object:
    """Create HdfilmePlugin instance."""
    return _HdfilmePlugin()


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<div class="item relative mt-3">
  <div class="flex flex-col h-full">
    <a class="block relative"
       href="/filme1/23004-batman-stream.html"
       title="Batman">
      <figure><img alt="Batman poster"></figure>
    </a>
    <a class="movie-title" title="Batman"
       href="/filme1/23004-batman-stream.html">
      <h3> Batman </h3>
    </a>
    <p> Batman </p>
    <div class="flex-grow flex items-end">
      <div class="meta text-gray-400">
        <span>1989</span>
        <i class="dot"></i>
        <span>126 min</span>
        <span class="absolute right-0"> HD </span>
      </div>
    </div>
  </div>
</div>

<div class="item relative mt-3">
  <div class="flex flex-col h-full">
    <a class="block relative"
       href="/filme1/39187-the-batman-stream.html"
       title="The Batman">
      <figure><img alt="poster"></figure>
    </a>
    <a class="movie-title" title="The Batman"
       href="/filme1/39187-the-batman-stream.html">
      <h3> The Batman </h3>
    </a>
    <p> The Batman </p>
    <div class="flex-grow flex items-end">
      <div class="meta text-gray-400">
        <span>2004</span>
        <i class="dot"></i>
        <span>20 min</span>
        <span class="absolute right-0"> HD </span>
      </div>
    </div>
  </div>
</div>
</body></html>
"""

_FILM_DETAIL_HTML = """\
<html><body>
<script src="https://meinecloud.click/ddl/tt0096895" type="text/javascript"></script>
<section class=" detail mt-5">
  <div class="md:flex items-center">
    <div class="poster md:flex-none text-center">
      <figure><img alt="Batman hdfilme stream online"></figure>
    </div>
    <div class="info md:pl-5 md:flex-grow">
      <h1 class="font-bold">Batman hdfilme</h1>
      <div class="border-b border-gray-700 font-extralight mb-3">
        <span>
          <a href="https://hdfilme.legal/fantasy/">Fantasy</a>&nbsp;
          <a href="https://hdfilme.legal/action/">Action</a>&nbsp;
          <a href="https://hdfilme.legal/krimi/">Krimi</a>
        </span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>
          <a href="/xfsearch/country/USA/">USA</a>
        </span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>1989</span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>126 min</span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>HD</span>
      </div>
    </div>
  </div>
  <div class="bg-gray-1000 rounded p-5">
    <div class="border-b border-gray-700 mb-5 pb-5">
      <section>
        <h2 class="uppercase">Batman (1989) stream kostenlos online legal:</h2>
        <div class="font-extralight prose max-w-none">
          <p>Gotham City erstickt in einem Sumpf...</p>
          <p>Referenzen von <a href="https://www.themoviedb.org/movie/268"
            title="Batman" rel="nofollow" target="_blank">Themoviedb</a></p>
        </div>
      </section>
    </div>
  </div>
</section>
</body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body>
<section class=" detail mt-5">
  <div class="md:flex items-center">
    <div class="info md:pl-5 md:flex-grow">
      <h1 class="font-bold">Stranger Things hdfilme</h1>
      <div class="border-b border-gray-700 font-extralight mb-3">
        <span>
          <a href="https://hdfilme.legal/serien/">Serien</a>&nbsp;
          <a href="https://hdfilme.legal/drama/">Drama</a>&nbsp;
          <a href="https://hdfilme.legal/sci-fi/">Sci-Fi</a>
        </span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>Staffel/Episode: 5x08</span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>2016</span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>50 min</span>
        <span class="align-text-bottom divider text-gray-500">|</span>
        <span>HD/Deutsch</span>
      </div>
    </div>
  </div>
  <div class="bg-gray-1000 rounded p-5">
    <section>
      <h2 class="uppercase">Stranger Things (2016) stream serien kostenlos online:</h2>
      <div class="font-extralight prose max-w-none">
        <p>Nach dem Verschwinden eines Jungen...</p>
        <p>Referenzen von <a href="https://www.themoviedb.org/tv/66732"
          title="Stranger Things" rel="nofollow" target="_blank">Themoviedb</a></p>
      </div>
    </section>
  </div>
  <div class="su-spoiler-btn">Komplette Linkliste</div>
  <div class="su-spoiler su-spoiler-style-default">
    <div class="su-spoiler-title" data-toggle="collapse" data-target="#se-ac-1">
      <span class="su-spoiler-icon"></span>Staffel 1
    </div>
    <div class="su-spoiler-content su-clearfix collapse"
         id="se-ac-1">
      1x1 Episode 1 \u2013
      <a href="https://supervideo.tv/abc123.html">Supervideo</a>
      &nbsp; <a href="https://dropload.io/def456">Dropload</a>
      &nbsp;<a href="/engine/player.php?id=29460&amp;s=1">
        Player HD/4K</a><br>
      1x2 Episode 2 \u2013
      <a href="https://supervideo.tv/ghi789.html">Supervideo</a>
      &nbsp; <a href="https://dropload.io/jkl012">Dropload</a>
      <br>
    </div>
  </div>
  <div class="su-spoiler su-spoiler-style-default">
    <div class="su-spoiler-title" data-toggle="collapse" data-target="#se-ac-2">
      <span class="su-spoiler-icon"></span>Staffel 2
    </div>
    <div class="su-spoiler-content su-clearfix collapse"
         id="se-ac-2">
      2x1 Episode 1 \u2013
      <a href="https://supervideo.tv/mno345.html">Supervideo</a>
      &nbsp; <a href="https://dropload.io/pqr678">Dropload</a>
      <br>
    </div>
  </div>
</section>
</body></html>
"""


def _mc_line(url: str, hoster: str, size: str) -> str:
    """Build one meinecloud document.write() line."""
    bs = "\\"
    dq = bs + '"'
    sq = bs + "'"
    return (
        f"document.write('<a onclick={dq}"
        f"window.open({sq}{url}{sq}){dq}"
        f" class={dq}streams{dq}>"
        f"<span class={dq}streaming{dq}>"
        f"{hoster}</span>"
        f"<mark>1080p</mark>"
        f"<span style={dq}color:#999;{dq}>"
        f"{size}</span></a>')"
    )


_MEINECLOUD_SCRIPT = "\n".join(
    [
        "document.write('<div id=\\\"streams\\\">')",
        _mc_line(
            "https://supervideo.cc/c12pdf9x7oi4",
            "Supervideo",
            "1.0GB",
        ),
        _mc_line(
            "https://dropload.tv/i6yeojjbilif",
            "Dropload",
            "1.2GB",
        ),
        _mc_line(
            "https://doodstream.com/d/jud9plfzzhf0",
            "Doodstream",
            "1.1GB",
        ),
        "document.write('</div>')",
    ]
)


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://hdfilme.legal")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Batman"
        assert first["url"] == "https://hdfilme.legal/filme1/23004-batman-stream.html"
        assert first["year"] == "1989"
        assert first["duration"] == "126 min"
        assert first["quality"] == "HD"

        second = parser.results[1]
        assert second["title"] == "The Batman"
        assert second["year"] == "2004"

    def test_empty_page_returns_no_results(self) -> None:
        html = "<html><body><div class='content'>Nothing here</div></body></html>"
        parser = _SearchResultParser("https://hdfilme.legal")
        parser.feed(html)
        assert len(parser.results) == 0

    def test_item_without_movie_title_skipped(self) -> None:
        html = """\
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <div class="meta">
              <span>2020</span>
            </div>
          </div>
        </div>
        """
        parser = _SearchResultParser("https://hdfilme.legal")
        parser.feed(html)
        assert len(parser.results) == 0


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_film_detail(self) -> None:
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(_FILM_DETAIL_HTML)

        assert parser.imdb_id == "tt0096895"
        assert parser.title == "Batman"
        assert parser.year == "1989"
        assert parser.duration == "126 min"
        assert parser.quality == "HD"
        assert "Fantasy" in parser.genres
        assert "Action" in parser.genres
        assert "Krimi" in parser.genres
        assert parser.is_series is False
        assert "themoviedb.org/movie/268" in parser.tmdb_url

    def test_parses_series_detail(self) -> None:
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.title == "Stranger Things"
        assert parser.is_series is True
        assert parser.year == "2016"
        assert "Serien" in parser.genres
        assert "Drama" in parser.genres
        assert "themoviedb.org/tv/66732" in parser.tmdb_url

    def test_series_episode_links(self) -> None:
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(_SERIES_DETAIL_HTML)

        # Should have episode links from both seasons
        # Filtering out /engine/player.php links
        assert len(parser.episode_links) >= 4
        hosters = [e["hoster"] for e in parser.episode_links]
        assert "supervideo" in hosters
        assert "dropload" in hosters

        # Check full URLs
        for link in parser.episode_links:
            assert link["link"].startswith("https://")

    def test_series_detected_by_serien_genre(self) -> None:
        html = """\
        <div class="info md:pl-5 md:flex-grow">
          <div class="border-b border-gray-700 font-extralight mb-3">
            <span>
              <a href="https://hdfilme.legal/serien/">Serien</a>
            </span>
          </div>
        </div>
        """
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(html)
        assert parser.is_series is True

    def test_series_detected_by_tmdb_tv_url(self) -> None:
        html = '<a href="https://www.themoviedb.org/tv/12345">TMDB</a>'
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(html)
        assert parser.is_series is True

    def test_series_detected_by_h2_text(self) -> None:
        html = "<h2>Test (2020) stream serien kostenlos online:</h2>"
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(html)
        assert parser.is_series is True

    def test_series_detected_by_spoiler_content(self) -> None:
        html = """\
        <div class="su-spoiler-content su-clearfix collapse" id="se-ac-1">
          1x1 Episode 1 - <a href="https://supervideo.tv/test.html">Supervideo</a>
        </div>
        """
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(html)
        assert parser.is_series is True

    def test_empty_detail_page(self) -> None:
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed("<html><body></body></html>")
        assert parser.imdb_id == ""
        assert parser.is_series is False
        assert len(parser.genres) == 0
        assert len(parser.episode_links) == 0

    def test_imdb_id_from_imdb_link(self) -> None:
        html = """\
        <a href="https://www.imdb.com/title/tt4574334" title="IMDb">IMDb</a>
        """
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(html)
        assert parser.imdb_id == "tt4574334"
        assert "imdb.com/title/tt4574334" in parser.imdb_url

    def test_h1_title_strips_hdfilme_suffix(self) -> None:
        html = '<h1 class="font-bold">Test Movie hdfilme</h1>'
        parser = _DetailPageParser("https://hdfilme.legal")
        parser.feed(html)
        assert parser.title == "Test Movie"


class TestMeineCloudParser:
    """Tests for _parse_meinecloud_script."""

    def test_parses_stream_links(self) -> None:
        links = _parse_meinecloud_script(_MEINECLOUD_SCRIPT)

        assert len(links) == 3

        supervideo = links[0]
        assert supervideo["hoster"] == "supervideo"
        assert supervideo["link"] == "https://supervideo.cc/c12pdf9x7oi4"
        assert supervideo["quality"] == "1080p"
        assert supervideo["size"] == "1.0GB"

        dropload = links[1]
        assert dropload["hoster"] == "dropload"
        assert dropload["link"] == "https://dropload.tv/i6yeojjbilif"

        doodstream = links[2]
        assert doodstream["hoster"] == "doodstream"
        assert doodstream["link"] == "https://doodstream.com/d/jud9plfzzhf0"

    def test_empty_script(self) -> None:
        links = _parse_meinecloud_script("")
        assert links == []

    def test_no_window_open(self) -> None:
        links = _parse_meinecloud_script("document.write('hello')")
        assert links == []

    def test_extracts_from_url_domain_fallback(self) -> None:
        script = r"window.open('https://newsite.example.com/abc123')"
        links = _parse_meinecloud_script(script)
        assert len(links) == 1
        assert links[0]["hoster"] == "newsite"
        assert links[0]["link"] == "https://newsite.example.com/abc123"


# ---------------------------------------------------------------------------
# Plugin integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "https://hdfilme.legal/"),
    )


class TestHdfilmePlugin:
    """Tests for HdfilmePlugin with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_search_returns_film_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_FILM_DETAIL_HTML),  # Batman detail
                _mock_response(_MEINECLOUD_SCRIPT),  # meinecloud for Batman
                _mock_response(_FILM_DETAIL_HTML),  # The Batman detail
                _mock_response(_MEINECLOUD_SCRIPT),  # meinecloud for The Batman
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 2
        assert results[0].title == "Batman"
        assert results[0].category == 2000  # Film
        assert results[0].download_link.startswith("https://")

    @pytest.mark.asyncio
    async def test_search_returns_series_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        # Search returns one result, detail shows it's a series
        search_html = """\
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="Stranger Things"
               href="/filme1/29460-stranger-things-stream.html">
              <h3> Stranger Things </h3>
            </a>
            <div class="meta"><span>2016</span></div>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page
                _mock_response(_SERIES_DETAIL_HTML),  # series detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("Stranger Things")

        assert len(results) == 1
        assert results[0].title == "Stranger Things"
        assert results[0].category == 5000  # Series
        assert len(results[0].download_links) >= 4

    @pytest.mark.asyncio
    async def test_search_empty_query(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        empty_html = "<html><body>No results</body></html>"
        mock_client.get = AsyncMock(return_value=_mock_response(empty_html))

        plug._client = mock_client
        results = await plug.search("xy")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_handles_http_error(self) -> None:
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
                httpx.ConnectError("detail failed"),  # Batman detail
                httpx.ConnectError("detail failed"),  # The Batman detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_meinecloud_error_returns_empty_links(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_FILM_DETAIL_HTML),  # Batman detail
                httpx.ConnectError("meinecloud failed"),  # meinecloud
                _mock_response(_FILM_DETAIL_HTML),  # The Batman detail
                httpx.ConnectError("meinecloud failed"),  # meinecloud
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        # No results because meinecloud failed → no download links
        assert results == []

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_called_once()
        assert plug._client is None

    @pytest.mark.asyncio
    async def test_film_result_has_download_links(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="Batman"
               href="/filme1/23004-batman-stream.html">
              <h3> Batman </h3>
            </a>
            <div class="meta"><span>1989</span></div>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page
                _mock_response(_FILM_DETAIL_HTML),  # detail
                _mock_response(_MEINECLOUD_SCRIPT),  # meinecloud
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        assert len(results) == 1
        first = results[0]
        assert first.download_links is not None
        assert len(first.download_links) == 3
        assert first.download_link.startswith("https://")

    @pytest.mark.asyncio
    async def test_film_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="Batman"
               href="/filme1/23004-batman-stream.html">
              <h3> Batman </h3>
            </a>
            <div class="meta"><span>1989</span></div>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page
                _mock_response(_FILM_DETAIL_HTML),  # detail
                _mock_response(_MEINECLOUD_SCRIPT),  # meinecloud
            ]
        )

        plug._client = mock_client
        results = await plug.search("Batman")

        first = results[0]
        assert first.metadata.get("year") == "1989"
        assert "Action" in first.metadata.get("genres", "")
        assert first.metadata.get("imdb_id") == "tt0096895"
        assert first.metadata.get("tmdb_url") is not None

    @pytest.mark.asyncio
    async def test_category_filter_series_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        # Search returns mixed results, but one detail is series, other is film
        search_html = """\
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="Batman"
               href="/filme1/23004-batman-stream.html">
              <h3> Batman </h3>
            </a>
            <div class="meta"><span>1989</span></div>
          </div>
        </div>
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="Stranger Things"
               href="/filme1/29460-stranger-things-stream.html">
              <h3> Stranger Things </h3>
            </a>
            <div class="meta"><span>2016</span></div>
          </div>
        </div>
        """

        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(search_html),  # search page
                _mock_response(_FILM_DETAIL_HTML),  # Batman detail (film)
                _mock_response(_MEINECLOUD_SCRIPT),  # meinecloud for Batman
                _mock_response(_SERIES_DETAIL_HTML),  # Stranger Things (series)
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=5000)

        # Only series should remain
        assert len(results) == 1
        assert results[0].title == "Stranger Things"

    @pytest.mark.asyncio
    async def test_browse_category_no_query(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        browse_html = """\
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="Some Movie"
               href="/filme1/12345-some-movie-stream.html">
              <h3> Some Movie </h3>
            </a>
            <div class="meta"><span>2023</span></div>
          </div>
        </div>
        """

        # First call is browse page, then detail + meinecloud
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(browse_html),  # browse page 1
                _mock_response(""),  # browse page 2 (empty)
                _mock_response(_FILM_DETAIL_HTML),  # detail
                _mock_response(_MEINECLOUD_SCRIPT),  # meinecloud
            ]
        )

        plug._client = mock_client
        results = await plug.search("", category=2000)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_no_query_no_category_returns_empty(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        results = await plug.search("")
        assert results == []
