"""Tests for the hdfilme.cafe Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

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
<iframe src="https://devideosrc.co/movie/tt0096895" allowfullscreen></iframe>
<section class=" detail mt-5">
  <div class="md:flex items-center">
    <div class="poster md:flex-none text-center">
      <figure><img alt="Batman hdfilme stream online"></figure>
    </div>
    <div class="info md:pl-5 md:flex-grow">
      <h1 class="font-bold">Batman hdfilme</h1>
      <div class="border-b border-gray-700 font-extralight mb-3">
        <span>
          <a href="https://hdfilme.cafe/fantasy/">Fantasy</a>&nbsp;
          <a href="https://hdfilme.cafe/action/">Action</a>&nbsp;
          <a href="https://hdfilme.cafe/krimi/">Krimi</a>
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
          <a href="https://hdfilme.cafe/serien/">Serien</a>&nbsp;
          <a href="https://hdfilme.cafe/drama/">Drama</a>&nbsp;
          <a href="https://hdfilme.cafe/sci-fi/">Sci-Fi</a>
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
  <iframe id="serial_iframe" src="" allowfullscreen></iframe>
  <script>
  (function() {
    var imdb = 'tt4574334';
    var iframe = document.getElementById('serial_iframe');
    iframe.src = 'https://devideosrc.co/serial/' + imdb;
  })();
  </script>
</section>
</body></html>
"""


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://hdfilme.cafe")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Batman"
        assert first["url"] == "https://hdfilme.cafe/filme1/23004-batman-stream.html"
        assert first["year"] == "1989"
        assert first["duration"] == "126 min"
        assert first["quality"] == "HD"

        second = parser.results[1]
        assert second["title"] == "The Batman"
        assert second["year"] == "2004"

    def test_empty_page_returns_no_results(self) -> None:
        html = "<html><body><div class='content'>Nothing here</div></body></html>"
        parser = _SearchResultParser("https://hdfilme.cafe")
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
        parser = _SearchResultParser("https://hdfilme.cafe")
        parser.feed(html)
        assert len(parser.results) == 0


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_film_detail(self) -> None:
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(_FILM_DETAIL_HTML)

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
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.title == "Stranger Things"
        assert parser.is_series is True
        assert parser.year == "2016"
        assert "Serien" in parser.genres
        assert "Drama" in parser.genres
        assert "themoviedb.org/tv/66732" in parser.tmdb_url

    def test_series_detected_by_serien_genre(self) -> None:
        html = """\
        <div class="info md:pl-5 md:flex-grow">
          <div class="border-b border-gray-700 font-extralight mb-3">
            <span>
              <a href="https://hdfilme.cafe/serien/">Serien</a>
            </span>
          </div>
        </div>
        """
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(html)
        assert parser.is_series is True

    def test_series_detected_by_tmdb_tv_url(self) -> None:
        html = '<a href="https://www.themoviedb.org/tv/12345">TMDB</a>'
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(html)
        assert parser.is_series is True

    def test_series_detected_by_h2_text(self) -> None:
        html = "<h2>Test (2020) stream serien kostenlos online:</h2>"
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(html)
        assert parser.is_series is True

    def test_empty_detail_page(self) -> None:
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed("<html><body></body></html>")
        assert parser.imdb_id == ""
        assert parser.is_series is False
        assert len(parser.genres) == 0

    def test_imdb_id_from_imdb_link(self) -> None:
        html = """\
        <a href="https://www.imdb.com/title/tt4574334" title="IMDb">IMDb</a>
        """
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(html)
        assert parser.imdb_id == "tt4574334"
        assert "imdb.com/title/tt4574334" in parser.imdb_url

    def test_h1_title_strips_hdfilme_suffix(self) -> None:
        html = '<h1 class="font-bold">Test Movie hdfilme</h1>'
        parser = _DetailPageParser("https://hdfilme.cafe")
        parser.feed(html)
        assert parser.title == "Test Movie"


# ---------------------------------------------------------------------------
# Plugin tests (respx)
# ---------------------------------------------------------------------------

_BASE = "https://hdfilme.cafe"
_BATMAN_URL = _BASE + "/filme1/23004-batman-stream.html"
_THE_BATMAN_URL = _BASE + "/filme1/39187-the-batman-stream.html"
_ST_URL = _BASE + "/filme1/29460-stranger-things-stream.html"
_EMBED_LINKS = "https://devideosrc.co/api/embed-links"
_TOKEN_PAGE = '<script>body: JSON.stringify({ token: "dG9rZW4.abc" })</script>'

_MOVIE_LINKS_JSON = {
    "ok": True,
    "sources": [
        {
            "name": "supervideo.cc",
            "url": "https://supervideo.cc/e/c12pdf9x7oi4",
            "rank": 1,
        },
        {
            "name": "dropload.io",
            "url": "https://dr0pstream.com/e/i6yeojjbilif",
            "rank": 2,
        },
        {
            "name": "doodstream.com",
            "url": "https://doodstream.com/e/jud9plfzzhf0",
            "rank": 3,
        },
    ],
}

_SERIES_LINKS_JSON = {
    "ok": True,
    "tv": {
        "seasons": [
            {
                "season_number": 1,
                "episodes": [
                    {
                        "episode_number": 1,
                        "sources": [
                            {
                                "name": "supervideo.cc",
                                "url": "https://supervideo.cc/e/s1e1",
                            }
                        ],
                    },
                    {
                        "episode_number": 2,
                        "sources": [
                            {
                                "name": "dropload.io",
                                "url": "https://dr0pstream.com/e/s1e2",
                            }
                        ],
                    },
                ],
            },
            {
                "season_number": 2,
                "episodes": [
                    {
                        "episode_number": 1,
                        "sources": [
                            {
                                "name": "supervideo.cc",
                                "url": "https://supervideo.cc/e/s2e1",
                            }
                        ],
                    }
                ],
            },
        ]
    },
}


def _embed_links(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    payload = _MOVIE_LINKS_JSON if body["type"] == "movie" else _SERIES_LINKS_JSON
    return httpx.Response(200, json=payload)


def _search_html(*items: tuple[str, str]) -> str:
    return "".join(
        f"""
        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="movie-title" title="{title}" href="{href}"><h3> {title} </h3></a>
            <div class="meta"><span>2020</span></div>
          </div>
        </div>"""
        for title, href in items
    )


def _mock_site(listing: str, details: dict[str, str]) -> respx.Route:
    """Route search/browse listing, detail pages and devideosrc."""
    listing_route = respx.get(
        url__regex=rf"^{_BASE}/(\?.*|filme1/(page/\d+/)?|serien/(page/\d+/)?)$"
    ).mock(
        side_effect=lambda request: httpx.Response(
            200, text="" if "/page/" in request.url.path else listing
        )
    )
    for url, html in details.items():
        respx.get(url).respond(200, text=html)
    respx.get(url__startswith="https://devideosrc.co/movie/").respond(
        200, text=_TOKEN_PAGE
    )
    respx.get(url__startswith="https://devideosrc.co/serial/").respond(
        200, text=_TOKEN_PAGE
    )
    respx.post(_EMBED_LINKS).mock(side_effect=_embed_links)
    return listing_route


class TestHdfilmePlugin:
    """Tests for HdfilmePlugin with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_film_results(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _SEARCH_HTML,
            {_BATMAN_URL: _FILM_DETAIL_HTML, _THE_BATMAN_URL: _FILM_DETAIL_HTML},
        )

        results = await plug.search("Batman")
        await plug.cleanup()

        assert len(results) == 2
        assert results[0].title == "Batman"
        assert results[0].category == 2000  # Film
        assert [link["link"] for link in results[0].download_links] == [
            "https://supervideo.cc/e/c12pdf9x7oi4",
            "https://dr0pstream.com/e/i6yeojjbilif",
            "https://doodstream.com/e/jud9plfzzhf0",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_series_results(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _search_html(
                ("Stranger Things", "/filme1/29460-stranger-things-stream.html")
            ),
            {_ST_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("Stranger Things")
        await plug.cleanup()

        assert len(results) == 1
        assert results[0].title == "Stranger Things"
        assert results[0].category == 5000  # Series
        assert [link["label"] for link in results[0].download_links] == [
            "1x1 supervideo",
            "1x2 dropload",
            "2x1 supervideo",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_season_episode_filter(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _search_html(
                ("Batman", "/filme1/23004-batman-stream.html"),
                ("Stranger Things", "/filme1/29460-stranger-things-stream.html"),
            ),
            {_BATMAN_URL: _FILM_DETAIL_HTML, _ST_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("x", season=1, episode=2)
        await plug.cleanup()

        # the film is skipped, the series narrowed to 1x2
        assert [r.download_link for r in results] == ["https://dr0pstream.com/e/s1e2"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        plug = _make_plugin()
        _mock_site("<html><body>No results</body></html>", {})

        assert await plug.search("xy") == []
        await plug.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_handles_http_error(self) -> None:
        plug = _make_plugin()
        respx.get(url__startswith=_BASE).mock(side_effect=httpx.ConnectError("down"))

        assert await plug.search("test") == []
        await plug.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, {})
        respx.get(_BATMAN_URL).respond(500)
        respx.get(_THE_BATMAN_URL).respond(500)

        assert await plug.search("Batman") == []
        await plug.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_without_player_skips_result(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _search_html(("Batman", "/filme1/23004-batman-stream.html")),
            {_BATMAN_URL: "<html><body><h1>Batman hdfilme</h1></body></html>"},
        )

        assert await plug.search("Batman") == []
        await plug.cleanup()
        assert not any("devideosrc" in str(c.request.url) for c in respx.calls)

    @respx.mock
    @pytest.mark.asyncio
    async def test_devideosrc_error_returns_no_result(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _search_html(("Batman", "/filme1/23004-batman-stream.html")),
            {_BATMAN_URL: _FILM_DETAIL_HTML},
        )
        respx.post(_EMBED_LINKS).respond(500)

        assert await plug.search("Batman") == []
        await plug.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_film_result_metadata(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _search_html(("Batman", "/filme1/23004-batman-stream.html")),
            {_BATMAN_URL: _FILM_DETAIL_HTML},
        )

        results = await plug.search("Batman")
        await plug.cleanup()

        first = results[0]
        assert first.metadata.get("year") == "1989"
        assert "Action" in first.metadata.get("genres", "")
        assert first.metadata.get("imdb_id") == "tt0096895"
        assert "themoviedb.org/movie/268" in first.metadata.get("tmdb_url", "")

    @respx.mock
    @pytest.mark.asyncio
    async def test_category_filter_series_only(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _search_html(
                ("Batman", "/filme1/23004-batman-stream.html"),
                ("Stranger Things", "/filme1/29460-stranger-things-stream.html"),
            ),
            {_BATMAN_URL: _FILM_DETAIL_HTML, _ST_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("test", category=5000)
        await plug.cleanup()

        assert [r.title for r in results] == ["Stranger Things"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_browse_category_no_query(self) -> None:
        plug = _make_plugin()
        listing = _mock_site(
            _search_html(("Batman", "/filme1/23004-batman-stream.html")),
            {_BATMAN_URL: _FILM_DETAIL_HTML},
        )

        results = await plug.search("", category=2000)
        await plug.cleanup()

        assert len(results) == 1
        paths = [c.request.url.path for c in listing.calls]
        assert paths == ["/filme1/", "/filme1/page/2/"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_documentary_film_is_a_movie(self) -> None:
        """Documentaries used to be 5080 (TV) and dropped out of movie searches."""
        plug = _make_plugin()
        documentary = _FILM_DETAIL_HTML.replace(
            '<a href="https://hdfilme.cafe/fantasy/">Fantasy</a>',
            '<a href="https://hdfilme.cafe/dokumentation/">Dokumentation</a>',
        )
        _mock_site(
            _SEARCH_HTML, {_BATMAN_URL: documentary, _THE_BATMAN_URL: documentary}
        )

        results = await plug.search("Batman", category=2000)
        await plug.cleanup()

        assert [r.category for r in results] == [2000, 2000]

    @pytest.mark.asyncio
    async def test_no_query_no_category_returns_empty(self) -> None:
        plug = _make_plugin()
        plug._client = AsyncMock()

        assert await plug.search("") == []

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_called_once()
        assert plug._client is None


class TestLooseMatches:
    """Site searches also list loose matches; each one cost a detail page."""

    async def test_loose_matches_are_not_scraped(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_client = AsyncMock()
        plugin._verify_domain = AsyncMock()
        plugin._search_page = AsyncMock(
            return_value=[
                {"title": "Iron Man", "url": "https://site.example/1"},
                {"title": "The Iron Giant", "url": "https://site.example/2"},
            ]
        )
        plugin._scrape_detail = AsyncMock(return_value=[])

        await plugin.search("Iron Man")

        scraped = [c.args[0]["url"] for c in plugin._scrape_detail.await_args_list]
        assert scraped == ["https://site.example/1"]
