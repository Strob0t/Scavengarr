"""Tests for the streamcloud.download Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

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


_BASE = "https://streamcloud.download"


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
    <a href="https://streamcloud.download/7244-justice-league-stream-deutsch.html">\
<img src="/uploads/thumb/poster.jpg" alt="Justice League"><span class="overlay">\
</span></a>
  </div>
  <div class="f_title">\
<a href="https://streamcloud.download/7244-justice-league-stream-deutsch.html">\
Justice League</a></div>
  <div class="f_year">2017</div>
</div>

<div class="item cf item-video post-1515412 post type-post" style="position:relative;">
  <div class="thumb" title="The Batman">
    <a href="https://streamcloud.download/39187-the-batman-stream-deutsch.html">\
<img src="/uploads/thumb/poster2.jpg" alt="The Batman"><span class="overlay">\
</span></a>
  </div>
  <div class="f_title">\
<a href="https://streamcloud.download/39187-the-batman-stream-deutsch.html">\
The Batman</a></div>
  <div class="f_year">2004</div>
</div>
</div>
</body></html>
"""

# Movie: devideosrc movie player iframe; label values in <div>
_MOVIE_DETAIL_HTML = """\
<html><body><main>
<div id="movie_container">
<iframe src="https://devideosrc.co/movie/tt0974015" allowfullscreen></iframe>
</div>
<p>Bruce Wayne alias Batman hat wieder Vertrauen in die Menschheit.</p>
<div><strong>Genres: </strong> <div>Action / Abenteuer / Sci-Fi / Fantasy</div></div>
<div><strong>Veröffentlicht: </strong>
<div><a href="/xfsearch/2017">2017</a></div></div>
<div><strong>Spielzeit: </strong> <div>121 min</div></div>
<a href="https://www.imdb.com/title/tt0974015/">6.1/10</a>
<iframe src="https://devideosrc.co/embed/download/tt0974015"></iframe>
</main></body></html>
"""

# Series: player iframe filled by script; label values in <span>
_SERIES_DETAIL_HTML = """\
<html><body><main>
<iframe id="serial_iframe" src="" allowfullscreen></iframe>
<p>Joker hat ein Gas entwickelt, mit dem er ganz Gotham City beherrschen kann.</p>
<strong>Genres:</strong> <span>Serien / Animation / Action</span>
<strong>Veröffentlicht:</strong> <a href="/xfsearch/2004">2004</a>
<strong>Spielzeit:</strong> <span>20 min</span>
<a href="https://www.imdb.com/title/tt0398417/">7.4/10</a>
<script>
(function() {
  var imdb = 'tt0398417';
  var iframe = document.getElementById('serial_iframe');
  iframe.src = 'https://devideosrc.co/serial/' + imdb;
})();
</script>
</main></body></html>
"""

_EMPTY_DETAIL_HTML = """\
<html><body><main>
<h1>No Streams Available</h1>
<p>Short text.</p>
</main></body></html>
"""

_MOVIE_PLAYER = "https://devideosrc.co/movie/tt0974015"
_SERIES_PLAYER = "https://devideosrc.co/serial/tt0398417"
_EMBED_LINKS = "https://devideosrc.co/api/embed-links"


def _player_html(kind: str, imdb: str) -> str:
    return (
        "<script>fetch('/api/embed-links', {method: 'POST', "
        "body: JSON.stringify({ type: '" + kind + "', id: \"" + imdb + '", '
        'token: "dG9rZW4.abc123" }) })</script>'
    )


_MOVIE_LINKS_JSON = {
    "ok": True,
    "type": "movie",
    "sources": [
        {
            "id": "dropload-io-15",
            "name": "dropload.io",
            "url": "https://dr0pstream.com/e/g70obtkey4gw",
            "rank": 2,
        },
        {
            "id": "supervideo-cc-3",
            "name": "supervideo.cc",
            "url": "https://supervideo.cc/e/hvl13tlrh31o",
            "rank": 1,
        },
    ],
}

_SERIES_LINKS_JSON = {
    "ok": True,
    "type": "tv",
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
                                "url": "https://supervideo.cc/e/a7jl6afezqxu",
                            }
                        ],
                    },
                    {
                        "episode_number": 2,
                        "sources": [
                            {
                                "name": "supervideo.cc",
                                "url": "https://supervideo.cc/e/76jhsp47ukjk",
                            }
                        ],
                    },
                ],
            }
        ]
    },
}


def _embed_links(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    payload = _MOVIE_LINKS_JSON if body["type"] == "movie" else _SERIES_LINKS_JSON
    return httpx.Response(200, json=payload)


def _mock_site(search_html: str, details: dict[str, str]) -> None:
    """Route search (page 1 GET, page 2+ POST), detail pages and devideosrc."""
    respx.get(_BASE + "/").respond(200, text=search_html)
    respx.post(_BASE + "/index.php").respond(200, text="")
    for url, html in details.items():
        respx.get(url).respond(200, text=html)
    respx.get(_MOVIE_PLAYER).respond(200, text=_player_html("movie", "tt0974015"))
    respx.get(_SERIES_PLAYER).respond(200, text=_player_html("tv", "tt0398417"))
    respx.post(_EMBED_LINKS).mock(side_effect=_embed_links)


_JL_URL = _BASE + "/7244-justice-league-stream-deutsch.html"
_BATMAN_URL = _BASE + "/39187-the-batman-stream-deutsch.html"
_BOTH_DETAILS = {_JL_URL: _MOVIE_DETAIL_HTML, _BATMAN_URL: _SERIES_DETAIL_HTML}


def _single_search(title: str, url: str, year: str = "2024") -> str:
    return f"""\
<div class="item cf item-video">
  <div class="thumb" title="{title}"><a href="{url}"><img></a></div>
  <div class="f_title"><a href="{url}">{title}</a></div>
  <div class="f_year">{year}</div>
</div>
"""


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


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://streamcloud.download")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Justice League"
        assert (
            first["url"]
            == "https://streamcloud.download/7244-justice-league-stream-deutsch.html"
        )
        assert first["year"] == "2017"

        second = parser.results[1]
        assert second["title"] == "The Batman"
        assert second["year"] == "2004"

    def test_empty_page(self) -> None:
        parser = _SearchResultParser("https://streamcloud.download")
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
        parser = _SearchResultParser("https://streamcloud.download")
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
        parser = _SearchResultParser("https://streamcloud.download")
        parser.feed(html)
        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Fallback Title"


class TestDetailPageParser:
    """Tests for _DetailPageParser (metadata only)."""

    def test_movie_metadata_in_divs(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_MOVIE_DETAIL_HTML)

        assert parser.genres == ["Action", "Abenteuer", "Sci-Fi", "Fantasy"]
        assert parser.year == "2017"
        assert parser.runtime == "121 min"
        assert parser.imdb_id == "tt0974015"
        assert parser.imdb_rating == "6.1"
        assert parser.description.startswith("Bruce Wayne")
        assert parser.is_series is False

    def test_series_metadata_in_spans(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.genres == ["Serien", "Animation", "Action"]
        assert parser.runtime == "20 min"
        assert parser.imdb_rating == "7.4"
        assert parser.is_series is True

    def test_empty_page(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_EMPTY_DETAIL_HTML)

        assert parser.genres == []
        assert parser.imdb_id == ""
        assert parser.is_series is False


# ---------------------------------------------------------------------------
# Plugin integration tests (respx)
# ---------------------------------------------------------------------------


class TestStreamcloudPluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "streamcloud"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"

    def test_primary_domain(self) -> None:
        assert _StreamcloudPlugin().base_url == _BASE


class TestStreamcloudPluginSearch:
    """Tests for StreamcloudPlugin search with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("Batman")
        await plug.cleanup()

        # "Justice League" is a loose match of the site's search, not scraped
        assert {r.title for r in results} == {"The Batman"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_movie_links_from_devideosrc(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search("Justice League", _JL_URL, "2017"),
            {_JL_URL: _MOVIE_DETAIL_HTML},
        )

        results = await plug.search("Justice League")
        await plug.cleanup()

        assert len(results) == 1
        first = results[0]
        assert first.category == 2000
        # best rank first
        assert first.download_links == [
            {
                "hoster": "supervideo",
                "link": "https://supervideo.cc/e/hvl13tlrh31o",
                "label": "supervideo",
            },
            {
                "hoster": "dropload",
                "link": "https://dr0pstream.com/e/g70obtkey4gw",
                "label": "dropload",
            },
        ]
        assert first.download_link == "https://supervideo.cc/e/hvl13tlrh31o"

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_result(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search("The Batman", _BATMAN_URL, "2004"),
            {_BATMAN_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("The Batman")
        await plug.cleanup()

        assert len(results) == 1
        first = results[0]
        assert first.category == 5070  # Animation series → anime
        assert [link["label"] for link in first.download_links] == [
            "1x1 supervideo",
            "1x2 supervideo",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_episode_filter(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search("The Batman", _BATMAN_URL, "2004"),
            {_BATMAN_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("The Batman", season=1, episode=2)
        await plug.cleanup()

        assert [r.download_link for r in results] == [
            "https://supervideo.cc/e/76jhsp47ukjk"
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_missing_episode_skipped(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search("The Batman", _BATMAN_URL, "2004"),
            {_BATMAN_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("The Batman", season=3, episode=1)
        await plug.cleanup()

        assert results == []

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        plug = _make_plugin()
        plug._client = AsyncMock()

        assert await plug.search("") == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        plug = _make_plugin()
        _mock_site("<html><body>No results</body></html>", {})

        results = await plug.search("xyznonexistent")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_http_error(self) -> None:
        plug = _make_plugin()
        respx.get(_BASE + "/").mock(side_effect=httpx.ConnectError("down"))

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        url = _BASE + "/12345-test.html"
        _mock_site(_single_search("Test", url), {})
        respx.get(url).respond(500)

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_without_player_skips_result(self) -> None:
        plug = _make_plugin()
        url = _BASE + "/12345-test.html"
        _mock_site(_single_search("Test", url), {url: _EMPTY_DETAIL_HTML})

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []
        assert not any("devideosrc" in str(c.request.url) for c in respx.calls)

    @respx.mock
    @pytest.mark.asyncio
    async def test_devideosrc_error_skips_result(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search("Justice League", _JL_URL, "2017"),
            {_JL_URL: _MOVIE_DETAIL_HTML},
        )
        respx.post(_EMBED_LINKS).respond(500)

        results = await plug.search("Justice League")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search("Justice League", _JL_URL, "2017"),
            {_JL_URL: _MOVIE_DETAIL_HTML},
        )

        results = await plug.search("Justice League")
        await plug.cleanup()

        assert results[0].metadata == {
            "year": "2017",
            "genres": "Action, Abenteuer, Sci-Fi, Fantasy",
            "imdb_rating": "6.1",
            "imdb_id": "tt0974015",
            "runtime": "121 min",
        }


class TestStreamcloudCategoryFiltering:
    """Tests for category filtering."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_filter_movies_only(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("test", category=2000)
        await plug.cleanup()

        assert [r.title for r in results] == ["Justice League"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_filter_series_only(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("test", category=5000)
        await plug.cleanup()

        assert [r.title for r in results] == ["The Batman"]


class TestStreamcloudDomainFallback:
    """Tests for multi-domain fallback."""

    @pytest.mark.asyncio
    async def test_uses_first_working_domain(self) -> None:
        plug = _StreamcloudPlugin()
        mock_client = AsyncMock()

        # First domain fails, second succeeds
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.url = httpx.URL("https://streamcloud.my/")
        mock_client.head = AsyncMock(
            side_effect=[
                httpx.ConnectError("streamcloud.download down"),
                ok_resp,
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

        assert plug.base_url == "https://streamcloud.download"
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


class TestLooseMatches:
    """Site searches also list loose matches; each one cost a detail page."""

    async def test_loose_matches_are_not_scraped(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_client = AsyncMock()
        plugin._verify_domain = AsyncMock()
        plugin._search_all_pages = AsyncMock(
            return_value=[
                {"title": "Iron Man", "url": "https://site.example/1"},
                {"title": "The Iron Giant", "url": "https://site.example/2"},
            ]
        )
        plugin._scrape_detail = AsyncMock(return_value=None)

        await plugin.search("Iron Man")

        scraped = [c.args[0]["url"] for c in plugin._scrape_detail.await_args_list]
        assert scraped == ["https://site.example/1"]

    async def test_season_request_scrapes_the_exact_hit_only(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_client = AsyncMock()
        plugin._verify_domain = AsyncMock()
        plugin._search_all_pages = AsyncMock(
            return_value=[
                {"title": f"Dark {i}", "url": f"https://site.example/{i}"}
                for i in range(5)
            ]
            + [{"title": "Dark", "url": "https://site.example/dark"}]
        )
        plugin._scrape_detail = AsyncMock(return_value=None)

        await plugin.search("Dark", season=1)

        scraped = [c.args[0]["url"] for c in plugin._scrape_detail.await_args_list]
        assert scraped[0] == "https://site.example/dark"
        assert len(scraped) == 1  # "Dark 0" … are other titles
