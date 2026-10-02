"""Tests for the scnlog.me Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "scnlog.py"


def _load_module() -> ModuleType:
    """Load scnlog.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("scnlog_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_ScnlogPlugin = _mod.ScnlogPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser


def _make_plugin() -> object:
    return _ScnlogPlugin()


def _mock_response(html: str, url: str = "https://scnlog.me/test") -> MagicMock:
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
<html><body><ul class="rows">
<li class="row has-cat" data-id="1">
  <div class="row-body">
    <div class="title">
      <a href="/movies/batman-begins-2005/">
        <span class="title-start">Batman Begins (2005)</span></a>
    </div>
    <div class="meta"><span class="m">June 17th, 2026</span></div>
  </div>
</li>
<li class="row has-cat" data-id="2">
  <div class="row-body">
    <div class="title">
      <a href="/movies/the-dark-knight-2008/">
        <span class="title-start">The Dark Knight (2008)</span></a>
    </div>
  </div>
</li>
</ul></body></html>
"""

_SEARCH_WITH_PAGINATION_HTML = """
<html><body><ul class="rows">
<li class="row has-cat">
  <div class="row-body">
    <div class="title"><a href="/movies/result-one/"><span>Result One</span></a></div>
  </div>
</li>
</ul>
<div class="pagerwrap"><div class="pagination">
  <a class="pg" href="/movies/page/2/?s=batman">2</a>
  <a class="next pg" href="/movies/page/2/?s=batman">Next &raquo;</a>
</div></div>
</body></html>
"""

_PAGE2_HTML = """
<html><body><ul class="rows">
<li class="row has-cat">
  <div class="row-body">
    <div class="title"><a href="/movies/result-two/"><span>Result Two</span></a></div>
  </div>
</li>
</ul></body></html>
"""

_EMPTY_SEARCH_HTML = "<html><body><p>Nothing found</p></body></html>"

# One release per section, as the live search lists them
_MIXED_ROWS = [
    ("/movies/batman-2022/", "The.Batman.2022.1080p.BluRay.x264-GRP"),
    ("/foreign/batman-german/", "The.Batman.2022.German.DL.1080p.BluRay-GRP"),
    ("/foreign/caped-s02e01/", "Batman.Caped.Crusader.S02E01.German.1080p-GRP"),
    ("/tv-shows/caped-s02e02/", "Batman.Caped.Crusader.S02E02.1080p.WEB-GRP"),
    ("/ebooks/batman-comic/", "DC.Comics.Batman.2026.HYBRID.COMIC.eBook-GRP"),
]


async def _route_mixed_sections(url: str, **_kwargs: object) -> MagicMock:
    """Search pages list the rows of the searched section (all without one)."""
    url = str(url)
    if "?s=" not in url:
        return _mock_response(_DETAIL_HTML)
    section = "/" + url.split("scnlog.me/", 1)[1].split("?", 1)[0]
    return _mock_response(
        "".join(
            f'<li class="row has-cat"><div class="title"><a href="{href}">'
            f"<span>{title}</span></a></div></li>"
            for href, title in _MIXED_ROWS
            if href.startswith(section)
        )
    )


# Real-world markup: the download div opens inside a <p> and closes in one
_DETAIL_HTML = """
<html><body>
<h1 class="single-title">Batman Begins (2005) German DL 1080p</h1>
<h2>Download</h2>
<p></center><div class="download"></p>
<p><a href="https://rapidgator.net/file/abc" rel="nofollow">https://rapidgator.net/file/abc</a></p>
<p><a href="https://katfile.com/xyz" rel="nofollow">https://katfile.com/xyz</a></p>
<p><a href="https://ddownload.com/123" rel="nofollow">https://ddownload.com/123</a></p>
<p></div></p>
<nav class="post-nav"><div class="pn next"><a href="https://scnlog.me/next/">Next</a></div></nav>
</body></html>
"""

_DETAIL_NO_LINKS_HTML = """
<html><body>
<h1 class="single-title">Empty Release</h1>
<div class="download">
  <p>No links available</p>
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
        assert parser.results[0]["detail_url"] == "/movies/batman-begins-2005/"
        assert parser.results[1]["title"] == "The Dark Knight (2008)"

    def test_empty_search(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_EMPTY_SEARCH_HTML)
        assert parser.results == []

    def test_pagination_detected(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_WITH_PAGINATION_HTML)

        assert parser.next_page_url == "/movies/page/2/?s=batman"

    def test_no_pagination(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML)
        assert parser.next_page_url == ""


# ---------------------------------------------------------------------------
# Detail parser tests
# ---------------------------------------------------------------------------
class TestDetailPageParser:
    def test_extracts_title(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)
        assert parser.title == "Batman Begins (2005) German DL 1080p"

    def test_extracts_links(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert len(parser.links) == 3
        assert parser.links[0]["hoster"] == "rapidgator"
        assert parser.links[0]["link"] == "https://rapidgator.net/file/abc"
        assert parser.links[1]["hoster"] == "katfile"
        assert parser.links[2]["hoster"] == "ddownload"

    def test_no_links(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_NO_LINKS_HTML)
        assert parser.links == []


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------
class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "scnlog"

    def test_provides(self) -> None:
        plugin = _make_plugin()
        assert plugin.provides == "download"

    def test_domains(self) -> None:
        plugin = _make_plugin()
        assert "scnlog.me" in plugin._domains

    def test_base_url(self) -> None:
        plugin = _make_plugin()
        assert plugin.base_url == "https://scnlog.me"


# ---------------------------------------------------------------------------
# Search URL construction
# ---------------------------------------------------------------------------
class TestSearchUrl:
    @pytest.mark.asyncio
    async def test_builds_search_url_without_category(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search("batman")

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://scnlog.me/?s=batman"

    @pytest.mark.parametrize(
        ("category", "paths"),
        [
            (3000, {"music/"}),
            (3040, {"music/"}),
            (7000, {"ebooks/"}),
            (2010, {"foreign/"}),
            # Films and series also sit in foreign/ (German releases included)
            (2000, {"movies/", "foreign/"}),
            (2040, {"movies/", "foreign/"}),
            (5000, {"tv-shows/", "foreign/"}),
            (4000, {"apps/", "games/", "pda/"}),
        ],
    )
    @pytest.mark.asyncio
    async def test_search_urls_of_a_category(
        self, category: int, paths: set[str]
    ) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search("batman", category=category)

        assert {c[0][0] for c in mock_client.get.call_args_list} == {
            f"https://scnlog.me/{path}?s=batman" for path in paths
        }

    @pytest.mark.asyncio
    async def test_category_the_site_does_not_serve(self) -> None:
        # scnlog does not tell console games apart; 9999 is no category
        plugin = _make_plugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)

        assert await plugin.search("test", category=1000) == []
        assert await plugin.search("test", category=9999) == []
        plugin._client.get.assert_not_awaited()


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
                _mock_response(_DETAIL_HTML),  # detail 1
                _mock_response(_DETAIL_HTML),  # detail 2
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 2
        assert results[0].title == "Batman Begins (2005) German DL 1080p"
        assert results[0].download_link == "https://rapidgator.net/file/abc"
        assert len(results[0].download_links) == 3

    @pytest.mark.asyncio
    async def test_empty_search(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        results = await plugin.search("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_detail_no_links_skipped(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),
                _mock_response(_DETAIL_NO_LINKS_HTML),
                _mock_response(_DETAIL_HTML),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_results_are_labelled_by_their_section(self) -> None:
        """Results used to carry the requested category (or 2000)."""
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=_route_mixed_sections)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert sorted((r.source_url.split("/")[3], r.category) for r in results) == [
            ("ebooks", 7000),
            ("foreign", 2010),  # a film
            ("foreign", 5020),  # an episode
            ("movies", 2000),
            ("tv-shows", 5000),
        ]

    @pytest.mark.asyncio
    async def test_request_keeps_its_rows_before_loading_details(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=_route_mixed_sections)
        plugin._client = mock_client

        results = await plugin.search("batman", category=5000)

        assert sorted(r.category for r in results) == [5000, 5020]
        # tv-shows/ and foreign/ searched, then only the two TV details
        # (no film or eBook detail page)
        assert mock_client.get.await_count == 4

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        plugin._client = mock_client

        results = await plugin.search("batman")
        assert results == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class TestPagination:
    @pytest.mark.asyncio
    async def test_follows_next_page(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_WITH_PAGINATION_HTML),  # page 1
                _mock_response(_PAGE2_HTML),  # page 2
                _mock_response(_DETAIL_HTML),  # detail 1
                _mock_response(_DETAIL_HTML),  # detail 2
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 2
        titles = [r.title for r in results]
        assert "Batman Begins (2005) German DL 1080p" in titles

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_WITH_PAGINATION_HTML),
                _mock_response(_EMPTY_SEARCH_HTML),
                _mock_response(_DETAIL_HTML),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        # Only 1 search result from page 1
        assert len(results) == 1
