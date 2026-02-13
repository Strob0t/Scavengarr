"""Tests for the warezomen.com Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "warezomen.py"


def _load_module() -> ModuleType:
    """Load warezomen.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("warezomen_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_WarezomenPlugin = _mod.WarezomenPlugin
_SearchResultParser = _mod._SearchResultParser
_slugify = _mod._slugify


def _make_plugin() -> object:
    return _WarezomenPlugin()


def _mock_response(html: str, url: str = "https://warezomen.com/test") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = html
    resp.url = url
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------
_RESULT_TABLE_HTML = """
<html><body>
<table class="download">
<tbody>
  <tr>
    <td class="d" colspan="4"></td>
  </tr>
  <tr>
    <td class="n"><a rel="nofollow" title="Avatar.2009.1080p.BluRay"
       href="https://apps4all.com/dl/avatar">Avatar.2009.108...</a></td>
    <td class="n">apps4all</td>
    <td class="t2">Movie</td>
    <td>01-Nov-2025</td>
  </tr>
  <tr>
    <td class="d" colspan="4"></td>
  </tr>
  <tr>
    <td class="n"><a rel="nofollow" title="Breaking.Bad.S01.720p"
       href="https://alphaddl.com/dl/bb">Breaking.Bad.S0...</a></td>
    <td class="n">alphaddl</td>
    <td class="t2">TV</td>
    <td>15-Oct-2025</td>
  </tr>
  <tr>
    <td class="n"><a rel="nofollow" title="Photoshop.2024.Portable"
       href="https://freshwap.cc/dl/ps">Photoshop.2024....</a></td>
    <td class="n">freshwap</td>
    <td class="t2">Software</td>
    <td>10-Sep-2025</td>
  </tr>
</tbody>
</table>
</body></html>
"""

_EMPTY_TABLE_HTML = """
<html><body>
<table class="download">
<tbody>
  <tr><td class="d" colspan="4"></td></tr>
</tbody>
</table>
</body></html>
"""

_NO_TABLE_HTML = "<html><body><p>No results found</p></body></html>"

_PAGINATION_HTML = """
<html><body>
<table class="download">
<tbody>
  <tr>
    <td class="n"><a rel="nofollow" title="Result.One"
       href="https://example.com/1">Result.One</a></td>
    <td class="n">example</td>
    <td class="t2">Movie</td>
    <td>01-Jan-2025</td>
  </tr>
</tbody>
</table>
<td id="pages">
  <a href="/download/test/2/">Next Page &gt;</a>
</td>
</body></html>
"""

_PAGE2_HTML = """
<html><body>
<table class="download">
<tbody>
  <tr>
    <td class="n"><a rel="nofollow" title="Result.Two"
       href="https://example.com/2">Result.Two</a></td>
    <td class="n">example</td>
    <td class="t2">Movie</td>
    <td>02-Jan-2025</td>
  </tr>
</tbody>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Slugify tests
# ---------------------------------------------------------------------------
class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Avatar 2009") == "avatar-2009"

    def test_special_chars(self) -> None:
        assert _slugify("Spider-Man: No Way Home") == "spider-man-no-way-home"

    def test_multiple_spaces(self) -> None:
        assert _slugify("hello   world") == "hello-world"

    def test_empty(self) -> None:
        assert _slugify("") == ""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------
class TestSearchResultParser:
    def test_extracts_results(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_RESULT_TABLE_HTML)

        assert len(parser.results) == 3
        titles = [r["title"] for r in parser.results]
        assert "Avatar.2009.1080p.BluRay" in titles
        assert "Breaking.Bad.S01.720p" in titles
        assert "Photoshop.2024.Portable" in titles

    def test_result_fields(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_RESULT_TABLE_HTML)

        avatar = parser.results[0]
        assert avatar["title"] == "Avatar.2009.1080p.BluRay"
        assert avatar["download_link"] == "https://apps4all.com/dl/avatar"
        assert avatar["published_date"] == "01-Nov-2025"

    def test_empty_table(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_EMPTY_TABLE_HTML)
        assert parser.results == []

    def test_no_table(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_NO_TABLE_HTML)
        assert parser.results == []

    def test_pagination_detected(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_PAGINATION_HTML)
        assert parser.next_page_url == "/download/test/2/"

    def test_no_pagination(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_RESULT_TABLE_HTML)
        assert parser.next_page_url == ""

    def test_separator_rows_skipped(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_RESULT_TABLE_HTML)
        # Separator rows have class="d", should not produce results
        for r in parser.results:
            assert r["title"] != ""
            assert r["download_link"] != ""


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------
class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "warezomen"

    def test_provides(self) -> None:
        plugin = _make_plugin()
        assert plugin.provides == "download"

    def test_domains(self) -> None:
        plugin = _make_plugin()
        assert "warezomen.com" in plugin._domains

    def test_base_url(self) -> None:
        plugin = _make_plugin()
        assert plugin.base_url == "https://warezomen.com"


# ---------------------------------------------------------------------------
# Search URL construction
# ---------------------------------------------------------------------------
class TestSearchUrl:
    @pytest.mark.asyncio
    async def test_builds_slugified_url(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        plugin._client = mock_client

        await plugin.search("Avatar 2009")

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://warezomen.com/download/avatar-2009/"

    @pytest.mark.asyncio
    async def test_special_chars_in_query(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        plugin._client = mock_client

        await plugin.search("Spider-Man: No Way Home")

        call_url = mock_client.get.call_args_list[0][0][0]
        assert call_url == "https://warezomen.com/download/spider-man-no-way-home/"


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------
class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_search_results(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        plugin._client = mock_client

        results = await plugin.search("avatar")

        assert len(results) == 3
        assert results[0].title == "Avatar.2009.1080p.BluRay"
        assert results[0].download_link == "https://apps4all.com/dl/avatar"

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_TABLE_HTML))
        plugin._client = mock_client

        results = await plugin.search("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_no_table_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_NO_TABLE_HTML))
        plugin._client = mock_client

        results = await plugin.search("nonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_category_passed_through(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        plugin._client = mock_client

        results = await plugin.search("avatar", category=5000)

        assert all(r.category == 5000 for r in results)

    @pytest.mark.asyncio
    async def test_default_category(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        plugin._client = mock_client

        results = await plugin.search("avatar")

        assert all(r.category == 2000 for r in results)

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        plugin._client = mock_client

        results = await plugin.search("avatar")
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
                _mock_response(_PAGINATION_HTML),
                _mock_response(_PAGE2_HTML),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert len(results) == 2
        titles = [r.title for r in results]
        assert "Result.One" in titles
        assert "Result.Two" in titles
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_stops_on_no_next_page(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        plugin._client = mock_client

        await plugin.search("test")

        # Only one request since no pagination link
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_PAGINATION_HTML),
                _mock_response(_EMPTY_TABLE_HTML),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert len(results) == 1
        assert mock_client.get.call_count == 2
