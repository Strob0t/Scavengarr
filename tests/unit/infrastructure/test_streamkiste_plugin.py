"""Tests for the streamkiste Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

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

_BASE = "https://streamkiste.bid"


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
{_player_script("tt0372784")}
</body></html>
"""

_SERIES_DETAIL_HTML = f"""\
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
{_player_script("tt4574334")}
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
    respx.get(_BASE + "/index.php").respond(200, text=search_html)
    respx.post(url__startswith=_BASE + "/index.php").respond(200, text="")
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


_BATMAN_URL = _BASE + "/film/12345-batman-begins.html"
_ST_URL = _BASE + "/film/67890-stranger-things.html"
_BOTH_DETAILS = {_BATMAN_URL: _DETAIL_HTML, _ST_URL: _SERIES_DETAIL_HTML}


def _single_search(title: str, href: str, release: str = "2025 - Action") -> str:
    return f"""\
<div class="movie-preview res_item">
  <div class="movie-title"><a href="{href}" title="{title}">{title}</a></div>
  <div class="movie-release">{release}</div>
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
        parser = _SearchResultParser("https://streamkiste.bid")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "Batman Begins"
        assert first["url"] == "https://streamkiste.bid/film/12345-batman-begins.html"
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
        parser = _SearchResultParser("https://streamkiste.bid")
        parser.feed("<html><body>No results</body></html>")
        assert len(parser.results) == 0

    def test_card_without_title_link_skipped(self) -> None:
        html = """\
        <div class="movie-preview res_item">
          <div class="movie-release">2025 - Action</div>
        </div>
        """
        parser = _SearchResultParser("https://streamkiste.bid")
        parser.feed(html)
        assert len(parser.results) == 0

    def test_anime_search_result(self) -> None:
        parser = _SearchResultParser("https://streamkiste.bid")
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
        parser = _SearchResultParser("https://streamkiste.bid")
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Test Movie"


class TestDetailPageParser:
    """Tests for _DetailPageParser (metadata only)."""

    def test_parses_metadata(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_DETAIL_HTML)

        assert parser.title == "Batman Begins"
        assert parser.year == "2025"
        assert parser.imdb_rating == "7.8"
        assert parser.genres == ["Action", "Abenteuer"]
        assert parser.is_series is False

    def test_parses_description(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_DETAIL_HTML)

        assert "Kampftechniken" in parser.description

    def test_series_detail(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.title == "Stranger Things"
        assert parser.is_series is True
        assert parser.year == "2024"
        assert parser.imdb_rating == "8.7"

    def test_empty_detail(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed(_EMPTY_DETAIL_HTML)

        assert parser.title == "No Streams"
        assert parser.is_series is False

    def test_imdb_rating_extraction(self) -> None:
        parser = _DetailPageParser(_BASE)
        parser.feed('<div class="average"><span>8.5</span></div>')
        assert parser.imdb_rating == "8.5"


# ---------------------------------------------------------------------------
# Plugin integration tests (respx)
# ---------------------------------------------------------------------------


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
            _single_search("Batman Begins", "/film/12345-batman-begins.html"),
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
                "Stranger Things", "/film/67890-stranger-things.html", "2024 - Serien"
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
                "Stranger Things", "/film/67890-stranger-things.html", "2024 - Serien"
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
    async def test_search_http_error(self) -> None:
        plug = _make_plugin()
        respx.get(_BASE + "/index.php").mock(side_effect=httpx.ConnectError("down"))

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        _mock_site(_single_search("Test", "/film/12345-test.html"), {})
        respx.get(_BASE + "/film/12345-test.html").respond(500)

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_without_player_skips_result(self) -> None:
        plug = _make_plugin()
        url = _BASE + "/film/12345-test.html"
        _mock_site(
            _single_search("Test", "/film/12345-test.html"), {url: _EMPTY_DETAIL_HTML}
        )

        results = await plug.search("test")
        await plug.cleanup()

        assert results == []
        assert not any("devideosrc" in str(c.request.url) for c in respx.calls)

    @respx.mock
    @pytest.mark.asyncio
    async def test_pagination_uses_post_for_page_2(self) -> None:
        plug = _make_plugin()
        _mock_site(_SEARCH_HTML, {})

        results = await plug._search_all_pages("test")
        await plug.cleanup()

        assert len(results) == 2
        methods = [c.request.method for c in respx.calls]
        assert methods == ["GET", "POST"]


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
                httpx.ConnectError("streamkiste.bid down"),
                httpx.ConnectError("streamkiste.taxi down"),
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

        assert plug.base_url == "https://streamkiste.bid"
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

    async def test_season_request_scrapes_the_closest_hits_only(self) -> None:
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
        assert len(scraped) == 3
