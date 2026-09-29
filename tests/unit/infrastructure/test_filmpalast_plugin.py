"""Tests for the filmpalast.to Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "filmpalast_to.py"


def _load_module() -> ModuleType:
    """Load filmpalast_to.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location(
        "filmpalast_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_FilmpalastPlugin = _mod.FilmpalastPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser


def _make_plugin() -> object:
    return _FilmpalastPlugin()


def _mock_response(html: str, url: str = "https://filmpalast.to/test") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = html
    resp.url = url
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------
_SEARCH_HTML = """
<html><body>
<article>
  <h2><a href="/stream/batman-begins-2005">Batman Begins (2005)</a></h2>
  <p>Some description</p>
</article>
<article>
  <h2><a href="/stream/the-dark-knight-2008">The Dark Knight (2008)</a></h2>
  <p>Another movie</p>
</article>
</body></html>
"""

_EMPTY_SEARCH_HTML = "<html><body><p>Keine Ergebnisse</p></body></html>"

# Pagination as served by the site: every page but the last links "vorwärts"
_PAGE_1_HTML = """
<html><body>
<article>
  <h2><a href="//filmpalast.to/stream/der-film-1">Der Film 1</a></h2>
</article>
<a class="pageing button-small rb active"  >1</a>
<a  class="pageing button-small rb"
    href='https://filmpalast.to/search/title/der/2'>2</a>
<a class="pageing button-small rb"
   href='https://filmpalast.to/search/title/der/2'> vorw&auml;rts&nbsp;+</a>
</body></html>
"""

_PAGE_2_HTML = """
<html><body>
<article>
  <h2><a href="//filmpalast.to/stream/der-film-2">Der Film 2</a></h2>
</article>
<a  class="pageing button-small rb"
    href='https://filmpalast.to/search/title/der/1' >-&nbsp;zur&uuml;ck</a>
<a class="pageing button-small rb"
   href='https://filmpalast.to/search/title/der/1' >1</a>
<a class="pageing button-small rb active"  >2</a>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
<h2 class="bgDark">Batman Begins (2005)</h2>
<span id="release_text">Batman.Begins.2005.German.DL.1080p.BluRay</span>
<span itemprop="description">A superhero movie</span>
<div id="grap-stream-list">
  <ul class="currentStreamLinks">
    <li>
      <p class="hostName">Voe</p>
      <a class="button iconPlay" data-player-url="https://voe.sx/e/abc123">Watch</a>
    </li>
    <li>
      <p class="hostName">Streamtape</p>
      <a class="button iconPlay" data-player-url="https://streamtape.com/e/xyz">Watch</a>
    </li>
  </ul>
</div>
</body></html>
"""

_DETAIL_HREF_LINK_HTML = """
<html><body>
<h2 class="bgDark">Movie Title</h2>
<div id="grap-stream-list">
  <ul class="currentStreamLinks">
    <li>
      <p class="hostName">Filemoon</p>
      <a class="button" href="https://filemoon.sx/e/test">Watch</a>
    </li>
  </ul>
</div>
</body></html>
"""

_DETAIL_ONCLICK_HTML = """
<html><body>
<h2 class="bgDark">Movie Title</h2>
<div id="grap-stream-list">
  <ul class="currentStreamLinks">
    <li>
      <p class="hostName">Mixdrop</p>
      <a class="button" onclick="window.open('https://mixdrop.ag/e/abc')">Watch</a>
    </li>
  </ul>
</div>
</body></html>
"""

_DETAIL_NO_LINKS_HTML = """
<html><body>
<h2 class="bgDark">Movie Title</h2>
<div id="grap-stream-list">
  <ul class="currentStreamLinks">
  </ul>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Search parser tests
# ---------------------------------------------------------------------------
class TestSearchResultParser:
    def test_extracts_results(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2
        assert parser.results[0]["title"] == "Batman Begins (2005)"
        assert parser.results[0]["detail_url"] == "/stream/batman-begins-2005"
        assert parser.results[1]["title"] == "The Dark Knight (2008)"

    def test_empty_search(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_EMPTY_SEARCH_HTML)
        assert parser.results == []
        assert parser.has_next_page is False

    def test_next_page_link(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_PAGE_1_HTML)
        assert parser.has_next_page is True
        # pagination links are no results
        assert [r["title"] for r in parser.results] == ["Der Film 1"]

    def test_last_page_has_no_next_page(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_PAGE_2_HTML)
        assert parser.has_next_page is False


# ---------------------------------------------------------------------------
# Detail parser tests
# ---------------------------------------------------------------------------
class TestDetailPageParser:
    def test_extracts_title_and_release(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.title == "Batman Begins (2005)"
        assert parser.release_name == "Batman.Begins.2005.German.DL.1080p.BluRay"

    def test_extracts_data_player_url(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert len(parser.links) == 2
        assert parser.links[0]["hoster"] == "Voe"
        assert parser.links[0]["link"] == "https://voe.sx/e/abc123"
        assert parser.links[1]["hoster"] == "Streamtape"
        assert parser.links[1]["link"] == "https://streamtape.com/e/xyz"

    def test_extracts_href_link(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HREF_LINK_HTML)

        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "Filemoon"
        assert parser.links[0]["link"] == "https://filemoon.sx/e/test"

    def test_extracts_onclick_link(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_ONCLICK_HTML)

        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "Mixdrop"
        assert parser.links[0]["link"] == "https://mixdrop.ag/e/abc"

    def test_no_links(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_NO_LINKS_HTML)
        assert parser.links == []


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------
class TestAccountLinks:
    def test_hoster_login_page_is_not_a_link(self) -> None:
        """filmpalast lists "Vixeo HD" with href vixeo.io/login (live, 2026-09)."""
        html = (
            '<div id="grap-stream-list"><ul>'
            '<li class="hostBg rb"><p class="hostName">Vixeo HD</p>'
            '<a class="button rb iconPlay" href="https://vixeo.io/login">Play</a>'
            "</li>"
            '<li class="hostBg rb"><p class="hostName">VOE HD</p>'
            '<a class="button rb iconPlay" href="https://voe.sx/xhoyeqr1jx6s">Play</a>'
            "</li></ul></div>"
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert [link["link"] for link in parser.links] == [
            "https://voe.sx/xhoyeqr1jx6s"
        ]


class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "filmpalast"

    def test_provides(self) -> None:
        plugin = _make_plugin()
        assert plugin.provides == "stream"

    def test_default_language(self) -> None:
        plugin = _make_plugin()
        assert plugin.default_language == "de"

    def test_base_url(self) -> None:
        plugin = _make_plugin()
        assert plugin.base_url == "https://filmpalast.to"


# ---------------------------------------------------------------------------
# Search URL
# ---------------------------------------------------------------------------
class TestSearchUrl:
    @pytest.mark.asyncio
    async def test_builds_search_url(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search("Batman")

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://filmpalast.to/search/title/Batman"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("query", "segment"),
        [
            ("Fast & Furious", "Fast%20%26%20Furious"),
            # "?" and "#" would end the path; the site finds the title without
            ("Wer ist Hanna?", "Wer%20ist%20Hanna"),
            # the site answers an encoded "/" with 404
            ("AC/DC", "AC%20DC"),
        ],
    )
    async def test_query_is_one_encoded_path_segment(
        self, query: str, segment: str
    ) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search(query)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == f"https://filmpalast.to/search/title/{segment}"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
_SEARCH_PAGES = {
    "https://filmpalast.to/search/title/der": _PAGE_1_HTML,
    "https://filmpalast.to/search/title/der/2": _PAGE_2_HTML,
}


def _paged_client() -> AsyncMock:
    """Serve the two search pages; every other URL is a detail page."""

    async def _get(url: str, **_: object) -> MagicMock:
        return _mock_response(_SEARCH_PAGES.get(url, _DETAIL_HTML), url=url)

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=_get)
    return client


class TestPagination:
    @pytest.mark.asyncio
    async def test_follows_pages_until_the_last(self) -> None:
        plugin = _make_plugin()
        plugin._client = _paged_client()

        results = await plugin.search("der")

        urls = [c.args[0] for c in plugin._client.get.call_args_list]
        assert urls[:2] == list(_SEARCH_PAGES)
        assert sorted(r.source_url for r in results) == [
            "https://filmpalast.to/stream/der-film-1",
            "https://filmpalast.to/stream/der-film-2",
        ]

    @pytest.mark.asyncio
    async def test_stops_at_max_results(self) -> None:
        plugin = _make_plugin()
        plugin._max_results = 1
        plugin._client = _paged_client()

        results = await plugin.search("der")

        urls = [c.args[0] for c in plugin._client.get.call_args_list]
        assert urls == [
            "https://filmpalast.to/search/title/der",
            "https://filmpalast.to/stream/der-film-1",
        ]
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Two-stage search
# ---------------------------------------------------------------------------
class TestSearch:
    @pytest.mark.asyncio
    async def test_full_pipeline(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page
                _mock_response(_DETAIL_HTML),  # detail page 1
                _mock_response(_DETAIL_HTML),  # detail page 2
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 2
        assert results[0].title == "Batman Begins (2005)"
        assert results[0].download_link == "https://voe.sx/e/abc123"
        assert len(results[0].download_links) == 2
        assert results[0].release_name == "Batman.Begins.2005.German.DL.1080p.BluRay"

    @pytest.mark.asyncio
    async def test_empty_search(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        results = await plugin.search("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_detail_with_no_links_skipped(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),
                _mock_response(_DETAIL_NO_LINKS_HTML),  # no links
                _mock_response(_DETAIL_HTML),  # has links
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        # Only second detail page has links
        assert len(results) == 1
        assert results[0].title == "Batman Begins (2005)"

    @pytest.mark.asyncio
    async def test_films_are_movies(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),
                _mock_response(_DETAIL_HTML),
                _mock_response(_DETAIL_HTML),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman", category=2000)

        assert [r.category for r in results] == [2000, 2000]

    @pytest.mark.asyncio
    async def test_series_request_skips_the_films(self) -> None:
        """The films used to come back labelled with the requested 5000."""
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_SEARCH_HTML))
        plugin._client = mock_client

        assert await plugin.search("batman", category=5000) == []
        mock_client.get.assert_awaited_once()  # no detail pages

    @pytest.mark.asyncio
    async def test_episodes_are_series_filtered_before_scraping(self) -> None:
        # Series are listed per episode (live: "The Walking Dead: Dead City S03E08")
        search_html = "".join(
            f'<article><h2><a href="/stream/{slug}">{title}</a></h2></article>'
            for slug, title in [
                ("twd-s03e07", "The Walking Dead S03E07"),
                ("twd-s03e08", "The Walking Dead S03E08"),
                ("twd-movie", "The Walking Dead Movie"),
            ]
        )
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[_mock_response(search_html), _mock_response(_DETAIL_HTML)]
        )
        plugin._client = mock_client

        results = await plugin.search("walking dead", season=3, episode=8)

        assert [r.source_url for r in results] == [
            "https://filmpalast.to/stream/twd-s03e08"
        ]
        assert results[0].category == 5000

    @pytest.mark.asyncio
    async def test_category_the_site_does_not_serve(self) -> None:
        plugin = _make_plugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)

        assert await plugin.search("batman", category=3000) == []
        plugin._client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        plugin._client = mock_client

        results = await plugin.search("batman")
        assert results == []

    @pytest.mark.asyncio
    async def test_detail_fetch_failure_skipped(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        search_resp = _mock_response(_SEARCH_HTML)
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 500
        error_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "error", request=MagicMock(), response=error_resp
            )
        )
        detail_resp = _mock_response(_DETAIL_HTML)

        mock_client.get = AsyncMock(side_effect=[search_resp, error_resp, detail_resp])
        plugin._client = mock_client

        results = await plugin.search("batman")

        # One detail page failed, one succeeded
        assert len(results) == 1
