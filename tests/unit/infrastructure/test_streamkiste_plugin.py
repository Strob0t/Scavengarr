"""Tests for the streamkiste Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.domain.plugins.base import PluginUnreachableError

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
<div class="grid">
  <a class="cv" href="/movie/12345-batman-begins-stream-kostenlos"
     title="Batman Begins">
    <div class="cv-art"><span class="qual"><b>HD</b></span></div>
    <div class="cv-t">Batman Begins</div>
    <div class="cv-m">2025</div>
  </a>
  <a class="cv" href="/movie/67890-stranger-things-stream-kostenlos"
     title="Stranger Things">
    <div class="cv-art"><span class="tag-serie">Serie</span></div>
    <div class="cv-t">Stranger Things</div>
    <div class="cv-m">Serie</div>
  </a>
</div>
</body></html>
"""

_BASE = "https://streamkiste.onl"


def _player_script(imdb: str) -> str:
    """The site embeds devideosrc's *series* player for every title."""
    return f"""
<iframe id="serial_iframe" src="" allowfullscreen></iframe>
<script>
(function() {{
  var imdb = '{imdb}';
  var iframe = document.getElementById('serial_iframe');
  iframe.src = 'https://devideosrc.co/serial/' + imdb;
}})();
</script>
"""


_DETAIL_HTML = f"""\
<html><head>
<meta property="og:type" content="video.movie">
<meta property="og:description" content="Batman Begins (2025) auf StreamKiste.">
<title>Batman Begins (2025) Stream Deutsch | StreamKiste</title>
</head><body>
<h1>Batman Begins</h1>
<span class="sk-det-jahr">(2025)</span>
<div class="sk-det-genres">
  <a href="/genre/action/">Action</a><a href="/genre/abenteuer/">Abenteuer</a>
</div>
<span class="sk-stern">7,8</span>
<div class="sk-det-trama"><p>Ein junger Bruce Wayne reist nach Osten, um dort
Kampftechniken zu erlernen.</p></div>
{_player_script("tt0372784")}
</body></html>
"""

_SERIES_DETAIL_HTML = f"""\
<html><head>
<meta property="og:type" content="video.tv_show">
<meta property="og:description" content="Stranger Things auf StreamKiste.">
<title>Stranger Things Stream Deutsch | StreamKiste</title>
</head><body>
<h1>Stranger Things</h1>
<span class="sk-det-jahr">(2024)</span>
<div class="sk-det-genres">
  <a href="/genre/drama/">Drama</a><a href="/genre/serien/">Serien</a>
</div>
<span class="sk-stern">8,7</span>
<div class="sk-det-trama"><p>Nach dem Verschwinden eines Jungen werden
Ereignisse aufgedeckt.</p></div>
{_player_script("tt4574334")}
</body></html>
"""

_EMPTY_DETAIL_HTML = """\
<html><body><h1>No Streams</h1></body></html>
"""

_EMBED_LINKS = "https://devideosrc.co/api/embed-links"
_TOKEN_PAGE = (
    "<script>body: JSON.stringify({ type: 'tv', id: \"x\", "
    'token: "dG9rZW4.abc123" })</script>'
)

_MOVIE_LINKS_JSON = {
    "ok": True,
    "sources": [
        {"name": "supervideo.cc", "url": "https://supervideo.cc/e/abc123", "rank": 1},
        {"name": "dropload.io", "url": "https://dr0pstream.com/e/xyz789", "rank": 2},
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
                                "url": "https://supervideo.cc/e/st101",
                            }
                        ],
                    },
                    {
                        "episode_number": 2,
                        "sources": [
                            {
                                "name": "supervideo.cc",
                                "url": "https://supervideo.cc/e/st102",
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
    """Route search, detail pages and devideosrc.

    Batman (tt0372784) is a movie: its series page has no token, the movie
    page does. Stranger Things (tt4574334) answers on the series page.
    """
    respx.get(_BASE + "/suche").respond(200, text=search_html)
    for url, html in details.items():
        respx.get(url).respond(200, text=html)
    respx.get(url__startswith="https://devideosrc.co/serial/tt0372784").respond(
        200, text="<html>no token</html>"
    )
    respx.get(url__startswith="https://devideosrc.co/movie/tt0372784").respond(
        200, text=_TOKEN_PAGE
    )
    respx.get(url__startswith="https://devideosrc.co/serial/tt4574334").respond(
        200, text=_TOKEN_PAGE
    )
    respx.post(_EMBED_LINKS).mock(side_effect=_embed_links)


_BATMAN_URL = _BASE + "/movie/12345-batman-begins-stream-kostenlos"
_ST_URL = _BASE + "/movie/67890-stranger-things-stream-kostenlos"
_BOTH_DETAILS = {_BATMAN_URL: _DETAIL_HTML, _ST_URL: _SERIES_DETAIL_HTML}


def _single_search(title: str, href: str, release: str = "2025") -> str:
    year = release[:4]
    return f"""\
<div class="grid"><a class="cv" href="{href}" title="{title}">
  <div class="cv-t">{title}</div><div class="cv-m">{year}</div>
</a></div>
"""


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


class TestStreamkistePluginAttributes:
    """Tests for plugin attributes."""

    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "streamkiste"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"

    def test_primary_domain(self) -> None:
        assert _StreamkistePlugin().base_url == _BASE


_CV_SEARCH_HTML = """\
<html><body>
<div class="grid">
  <a class="cv" href="/movie/27205-inception-stream-kostenlos"
     title="Inception" data-id="3700">
    <div class="cv-art">
      <img src="https://img.streamkiste.onl/t/p/w342/x.jpg" alt="Inception">
      <span class="qual"><b>HD</b><b>AC3</b></span>
    </div>
    <div class="cv-t">Inception</div>
    <div class="cv-m">2010</div>
  </a>
</div>
</body></html>
"""

_OG_MOVIE_HTML = """\
<html><head>
<meta property="og:type" content="video.movie">
<meta property="og:description" content="Inception (2010) auf StreamKiste
 kostenlos im Stream anschauen.">
<title>Inception (2010) Stream Deutsch – kostenlos anschauen | StreamKiste</title>
</head><body>
<h1>Inception</h1>
<iframe src="https://devideosrc.co/movie/tt1375666"></iframe>
</body></html>
"""

_OG_SERIES_HTML = """\
<html><head>
<meta property="og:type" content="video.tv_show">
<meta property="og:description" content="Dark auf StreamKiste: alle Staffeln.">
<title>Dark Stream Deutsch – alle Staffeln kostenlos | StreamKiste</title>
</head><body>
<h1>Dark</h1>
<iframe src="https://devideosrc.co/serial/tt5753856"></iframe>
</body></html>
"""

_SK_DETAIL_HTML = """\
<html><head>
<meta property="og:type" content="video.tv_show">
<meta property="og:description" content="Dark auf StreamKiste: alle Staffeln.">
<title>Dark Stream Deutsch – alle Staffeln kostenlos | StreamKiste</title>
</head><body>
<h1>Dark</h1>
<span class="sk-det-jahr">(2017)</span>
<div class="sk-det-genres">
  <a href="/genre/drama/">Drama</a><a href="/genre/krimi/">Krimi</a>
</div>
<span class="sk-stern">8,7</span>
<div class="sk-det-trama"><p>Die Geschichte der Familien.</p></div>
</body></html>
"""

_CV_DETAIL_HTML = """\
<html><head>
<meta property="og:type" content="video.movie">
<meta property="og:description" content="Inception (2010) auf StreamKiste.">
<title>Inception (2010) Stream Deutsch | StreamKiste</title>
</head><body>
<h1>Inception</h1>
<span class="sk-det-jahr">(2010)</span>
<div class="sk-det-genres"><a href="/genre/action/">Action</a>\
<a href="/genre/krimi/">Krimi</a>\
<a href="/genre/science-fiction/">Science Fiction</a></div>
<span class="sk-stern">8,8</span>
<div class="sk-det-trama"><p>Beim Versuch in das Unterbewusstsein
einzudringen.</p></div>
<iframe src="https://devideosrc.co/movie/tt1375666"></iframe>
</body></html>
"""


class TestCvSearchLayout:
    """The 2026-11 Next.js front end renders ``div.grid a.cv`` cards."""

    def test_cv_card_is_a_result(self) -> None:
        parser = _SearchResultParser("https://streamkiste.onl")
        parser.feed(_CV_SEARCH_HTML)

        assert len(parser.results) == 1
        result = parser.results[0]
        assert result["title"] == "Inception"
        assert result["url"] == (
            "https://streamkiste.onl/movie/27205-inception-stream-kostenlos"
        )
        assert result["year"] == "2010"
        assert result["quality"] == "HD"
        assert result["is_series"] is False

    def test_zero_hits_is_no_result(self) -> None:
        """The live "0 Treffer" page has no grid at all."""
        parser = _SearchResultParser("https://streamkiste.onl")
        parser.feed(
            '<p style="color:var(--muted)">0<!-- --> Treffer</p>'
            '<div class="empty">Nichts gefunden</div>'
        )
        assert parser.results == []

    def test_duplicate_cards_are_one_result(self) -> None:
        parser = _SearchResultParser("https://streamkiste.onl")
        parser.feed(_CV_SEARCH_HTML + _CV_SEARCH_HTML)
        assert len(parser.results) == 1

    def test_title_falls_back_to_the_title_attribute(self) -> None:
        parser = _SearchResultParser("https://streamkiste.onl")
        parser.feed(
            '<div class="grid"><a class="cv" href="/movie/1-x" title="X">'
            '<div class="cv-m">2019</div></a></div>'
        )
        assert parser.results[0]["title"] == "X"

    def test_title_falls_back_to_the_image_alt(self) -> None:
        parser = _SearchResultParser("https://streamkiste.onl")
        parser.feed(
            '<div class="grid"><a class="cv" href="/movie/2-y">'
            '<img alt="Y"><div class="cv-m">2018</div></a></div>'
        )
        assert parser.results[0]["title"] == "Y"

    def test_series_card_marks_the_series(self) -> None:
        parser = _SearchResultParser("https://streamkiste.onl")
        parser.feed(
            '<div class="grid"><a class="cv" href="/movie/70523-dark" '
            'title="Dark"><div class="cv-art"><span class="tag-serie">'
            'Serie</span></div><div class="cv-t">Dark</div>'
            '<div class="cv-m">Serie</div></a></div>'
        )
        assert parser.results[0]["is_series"] is True
        assert parser.results[0]["year"] == ""


class TestOpenGraphDetailLayout:
    """The 2026-11 detail page: Open Graph plus page-body metadata."""

    def test_movie_metadata(self) -> None:
        parser = _DetailPageParser("https://streamkiste.onl")
        parser.feed(_OG_MOVIE_HTML)

        assert parser.title == "Inception"
        assert parser.year == "2010"
        assert parser.is_series is False
        assert parser.description.startswith("Inception (2010) auf StreamKiste")

    def test_series_metadata(self) -> None:
        parser = _DetailPageParser("https://streamkiste.onl")
        parser.feed(_OG_SERIES_HTML)

        assert parser.title == "Dark"
        assert parser.is_series is True
        assert parser.description.startswith("Dark auf StreamKiste")

    def test_page_body_wins_over_the_blurb(self) -> None:
        parser = _DetailPageParser("https://streamkiste.onl")
        parser.feed(_SK_DETAIL_HTML)

        assert parser.year == "2017"
        assert parser.genres == ["Drama", "Krimi"]
        assert parser.imdb_rating == "8.7"
        assert parser.description == "Die Geschichte der Familien."

    def test_the_page_year_wins_over_the_title(self) -> None:
        """A wrong title year must not beat the page's own year."""
        parser = _DetailPageParser("https://streamkiste.onl")
        parser.feed(
            _SK_DETAIL_HTML.replace("(2017)", "(1974)").replace(
                "Dark Stream Deutsch",
                "Der Pate - Teil II (1972) Stream Deutsch",
            )
        )
        assert parser.year == "1974"

    def test_an_empty_plot_keeps_the_blurb(self) -> None:
        parser = _DetailPageParser("https://streamkiste.onl")
        parser.feed(_SK_DETAIL_HTML.replace("Die Geschichte der Familien.", "   "))
        assert parser.description == "Dark auf StreamKiste: alle Staffeln."


class TestStreamkistePluginSearch:
    """Tests for StreamkistePlugin search with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("Batman")
        await plug.cleanup()

        # "Stranger Things" is a loose match of the site's search, not scraped
        assert {r.title for r in results} == {"Batman Begins"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_movie_behind_series_player(self) -> None:
        """Series player without token -> movie player answers."""
        plug = _make_plugin()
        _mock_site(
            _single_search(
                "Batman Begins", "/movie/12345-batman-begins-stream-kostenlos"
            ),
            {_BATMAN_URL: _DETAIL_HTML},
        )

        results = await plug.search("Batman")
        await plug.cleanup()

        assert len(results) == 1
        first = results[0]
        assert first.category == 2000
        assert [link["link"] for link in first.download_links] == [
            "https://supervideo.cc/e/abc123",
            "https://dr0pstream.com/e/xyz789",
        ]
        assert first.metadata == {
            "year": "2025",
            "genres": "Action, Abenteuer",
            "imdb_rating": "7.8",
            "imdb_id": "tt0372784",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_result(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search(
                "Stranger Things",
                "/movie/67890-stranger-things-stream-kostenlos",
                "2024 - Serien",
            ),
            {_ST_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("Stranger Things")
        await plug.cleanup()

        assert len(results) == 1
        first = results[0]
        assert first.title == "Stranger Things"
        assert first.category == 5000
        assert [link["label"] for link in first.download_links] == [
            "1x1 supervideo",
            "1x2 supervideo",
        ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_series_episode_filter(self) -> None:
        plug = _make_plugin()
        _mock_site(
            _single_search(
                "Stranger Things",
                "/movie/67890-stranger-things-stream-kostenlos",
                "2024 - Serien",
            ),
            {_ST_URL: _SERIES_DETAIL_HTML},
        )

        results = await plug.search("Stranger Things", season=1, episode=2)
        await plug.cleanup()

        assert [r.download_link for r in results] == ["https://supervideo.cc/e/st102"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_season_request_drops_movies(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("x", season=1)
        await plug.cleanup()

        assert [r.title for r in results] == ["Stranger Things"]

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
    async def test_a_search_no_domain_serves_is_unreachable(self) -> None:
        """A failure in the plugin record, not a search without results."""
        plug = _make_plugin()
        refused = httpx.ConnectError("down")
        respx.get(url__startswith="https://").mock(side_effect=refused)
        respx.head(url__startswith="https://").mock(side_effect=refused)

        with pytest.raises(PluginUnreachableError):
            await plug.search("test")
        await plug.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        _mock_site(_single_search("Test", "/movie/12345-test-stream-kostenlos"), {})
        respx.get(_BASE + "/movie/12345-test-stream-kostenlos").respond(500)

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_without_player_skips_result(self) -> None:
        plug = _make_plugin()
        url = _BASE + "/movie/12345-test-stream-kostenlos"
        _mock_site(
            _single_search("Test", "/movie/12345-test-stream-kostenlos"),
            {url: _EMPTY_DETAIL_HTML},
        )

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []
        assert not any("devideosrc" in str(c.request.url) for c in respx.calls)

    @respx.mock
    @pytest.mark.asyncio
    async def test_one_page_only(self) -> None:
        """The 2026-11 front end has one result page and ignores paging."""
        plug = _make_plugin()
        _mock_site(_CV_SEARCH_HTML, {})

        results = await plug._search_all_pages("test")
        await plug.cleanup()

        assert len(results) == 1
        assert [c.request.method for c in respx.calls] == ["GET"]

    @pytest.mark.asyncio
    async def test_page_two_is_not_requested(self) -> None:
        plug = _make_plugin()
        assert await plug._search_page("test", 2) == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_page_two_sends_no_request(self) -> None:
        plug = _make_plugin()
        assert await plug._search_page("test", 2) == []
        assert not respx.calls

    @respx.mock
    @pytest.mark.asyncio
    async def test_cv_search_flows_to_a_stream(self) -> None:
        """A CV card, its detail page and the player make one stream."""
        plug = _make_plugin()
        detail_url = _BASE + "/movie/27205-inception-stream-kostenlos"
        respx.get(_BASE + "/suche").respond(
            200,
            text=(
                '<div class="grid"><a class="cv" '
                'href="/movie/27205-inception-stream-kostenlos" title="Inception">'
                '<div class="cv-t">Inception</div><div class="cv-m">2010</div>'
                "</a></div>"
            ),
        )
        respx.get(detail_url).respond(200, text=_CV_DETAIL_HTML)
        respx.get("https://devideosrc.co/movie/tt1375666").respond(
            200, text=_TOKEN_PAGE
        )
        respx.post(_EMBED_LINKS).mock(side_effect=_embed_links)

        results = await plug.search("Inception")
        await plug.cleanup()

        assert len(results) == 1
        result = results[0]
        assert result.category == 2000
        assert result.metadata["year"] == "2010"
        assert result.metadata["genres"] == "Action, Krimi, Science Fiction"
        assert result.description.startswith(
            "Action, Krimi, Science Fiction (2010) Beim Versuch"
        )
        assert result.download_links


class TestStreamkisteCategoryFiltering:
    """Tests for category filtering."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_filter_movies_only(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("test", category=2000)
        await plug.cleanup()

        assert [r.title for r in results] == ["Batman Begins"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_filter_series_only(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, _BOTH_DETAILS)

        results = await plug.search("test", category=5000)
        await plug.cleanup()

        assert [r.title for r in results] == ["Stranger Things"]


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


@respx.mock
async def test_a_search_page_that_times_out_is_a_timeout() -> None:
    """A slow site is a timeout in the plugin record, not a search without
    results."""
    respx.get("https://streamkiste.onl/suche").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    plugin = _make_plugin()
    plugin._client = httpx.AsyncClient()
    plugin._domain_verified = True

    with pytest.raises(TimeoutError):
        await plugin.search("batman")
