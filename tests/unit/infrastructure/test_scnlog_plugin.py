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
<html><body>
<div class="hentry">
  <div class="title">
    <h1><a href="/batman-begins-2005/">Batman Begins (2005)</a></h1>
  </div>
</div>
<div class="hentry">
  <div class="title">
    <h1><a href="/the-dark-knight-2008/">The Dark Knight (2008)</a></h1>
  </div>
</div>
</body></html>
"""

_SEARCH_WITH_PAGINATION_HTML = """
<html><body>
<div class="hentry">
  <div class="title">
    <h1><a href="/result-one/">Result One</a></h1>
  </div>
</div>
<div class="nav">
  <a href="/movies/?s=batman&paged=2">Next</a>
</div>
</body></html>
"""

_PAGE2_HTML = """
<html><body>
<div class="hentry">
  <div class="title">
    <h1><a href="/result-two/">Result Two</a></h1>
  </div>
</div>
</body></html>
"""

_EMPTY_SEARCH_HTML = "<html><body><p>Nothing found</p></body></html>"

_DETAIL_HTML = """
<html><body>
<div class="title"><h1>Batman Begins (2005) German DL 1080p</h1></div>
<div class="download">
  <p><a class="external" href="https://rapidgator.net/file/abc">Rapidgator</a></p>
  <p><a class="external" href="https://katfile.com/xyz">Katfile</a></p>
  <p><a class="external" href="https://ddownload.com/123">DDownload</a></p>
</div>
</body></html>
"""

_DETAIL_NO_LINKS_HTML = """
<html><body>
<div class="title"><h1>Empty Release</h1></div>
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
        assert parser.results[0]["detail_url"] == "/batman-begins-2005/"
        assert parser.results[1]["title"] == "The Dark Knight (2008)"

    def test_empty_search(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_EMPTY_SEARCH_HTML)
        assert parser.results == []

    def test_pagination_detected(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_WITH_PAGINATION_HTML)

        assert parser.next_page_url == "/movies/?s=batman&paged=2"

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
        assert parser.links[0]["hoster"] == "Rapidgator"
        assert parser.links[0]["link"] == "https://rapidgator.net/file/abc"
        assert parser.links[1]["hoster"] == "Katfile"
        assert parser.links[2]["hoster"] == "DDownload"

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

    def test_categories(self) -> None:
        plugin = _make_plugin()
        assert 2000 in plugin.categories
        assert 5000 in plugin.categories
        assert 4000 in plugin.categories


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

    @pytest.mark.asyncio
    async def test_builds_search_url_with_category(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search("batman", category=2000)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://scnlog.me/movies/?s=batman"

    @pytest.mark.asyncio
    async def test_builds_search_url_with_tv_category(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search("breaking bad", category=5000)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://scnlog.me/tv-shows/?s=breaking+bad"

    @pytest.mark.asyncio
    async def test_unknown_category_uses_no_path(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_SEARCH_HTML))
        plugin._client = mock_client

        await plugin.search("test", category=9999)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://scnlog.me/?s=test"


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
    async def test_category_passed_through(self) -> None:
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

        results = await plugin.search("batman", category=5000)
        assert all(r.category == 5000 for r in results)

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
