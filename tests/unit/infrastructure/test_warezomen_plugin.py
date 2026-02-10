"""Tests for the warezomen.com Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from scavengarr.domain.plugins.base import SearchResult

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "warezomen.py"


def _load_warezomen_module() -> ModuleType:
    """Load warezomen.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("warezomen_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_warez = _load_warezomen_module()
_WarezomenPlugin = _warez.WarezomenPlugin
_slugify = _warez._slugify
_ResultTableParser = _warez._ResultTableParser
_PaginationParser = _warez._PaginationParser
_CATEGORY_MAP = _warez._CATEGORY_MAP
_TORZNAB_TO_TYPES = _warez._TORZNAB_TO_TYPES


# -- Fixtures: HTML snippets ------------------------------------------------

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


# -- Slugify tests -----------------------------------------------------------


class TestSlugify:
    def test_basic_query(self) -> None:
        assert _slugify("Avatar 2009") == "avatar-2009"

    def test_multiple_spaces(self) -> None:
        assert _slugify("  breaking   bad  ") == "breaking-bad"

    def test_special_characters_stripped(self) -> None:
        assert _slugify("test!@#$%query") == "testquery"

    def test_hyphens_preserved(self) -> None:
        assert _slugify("spider-man") == "spider-man"

    def test_empty_query(self) -> None:
        assert _slugify("") == ""

    def test_only_special_chars(self) -> None:
        assert _slugify("!!!") == ""

    def test_unicode_stripped(self) -> None:
        assert _slugify("über cool") == "ber-cool"

    def test_mixed_case(self) -> None:
        assert _slugify("The Dark Knight") == "the-dark-knight"


# -- ResultTableParser tests -------------------------------------------------


class TestResultTableParser:
    def test_parses_multiple_rows(self) -> None:
        parser = _ResultTableParser()
        parser.feed(_RESULT_TABLE_HTML)

        assert len(parser.results) == 3

    def test_first_row_fields(self) -> None:
        parser = _ResultTableParser()
        parser.feed(_RESULT_TABLE_HTML)

        row = parser.results[0]
        assert row["title"] == "Avatar.2009.1080p.BluRay"
        assert row["download_link"] == "https://apps4all.com/dl/avatar"
        assert row["site"] == "apps4all"
        assert row["type"] == "Movie"
        assert row["date"] == "01-Nov-2025"

    def test_separator_rows_skipped(self) -> None:
        parser = _ResultTableParser()
        parser.feed(_RESULT_TABLE_HTML)

        # 2 separator rows + 3 data rows = only 3 results
        titles = [r["title"] for r in parser.results]
        assert "Avatar.2009.1080p.BluRay" in titles
        assert "Breaking.Bad.S01.720p" in titles
        assert "Photoshop.2024.Portable" in titles

    def test_empty_table(self) -> None:
        parser = _ResultTableParser()
        parser.feed(_EMPTY_TABLE_HTML)

        assert parser.results == []

    def test_no_table(self) -> None:
        parser = _ResultTableParser()
        parser.feed(_NO_TABLE_HTML)

        assert parser.results == []

    def test_row_without_link_skipped(self) -> None:
        html = """
        <table class="download"><tbody>
        <tr>
          <td class="n">No link here</td>
          <td class="n">site</td>
          <td class="t2">Movie</td>
          <td>01-Jan-2025</td>
        </tr>
        </tbody></table>
        """
        parser = _ResultTableParser()
        parser.feed(html)

        assert parser.results == []


# -- PaginationParser tests --------------------------------------------------


class TestPaginationParser:
    def test_next_page_found(self) -> None:
        parser = _PaginationParser()
        parser.feed(_PAGINATION_HTML)

        assert parser.next_url == "/download/test/2/"

    def test_no_pagination(self) -> None:
        parser = _PaginationParser()
        parser.feed(_RESULT_TABLE_HTML)

        assert parser.next_url is None

    def test_no_next_page_link(self) -> None:
        html = '<td id="pages"><a href="/download/test/1/">Previous Page</a></td>'
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.next_url is None


# -- Category mapping tests --------------------------------------------------


class TestCategoryMap:
    def test_movie_maps_to_2000(self) -> None:
        assert _CATEGORY_MAP["movie"] == 2000

    def test_tv_maps_to_5000(self) -> None:
        assert _CATEGORY_MAP["tv"] == 5000

    def test_music_maps_to_3000(self) -> None:
        assert _CATEGORY_MAP["music"] == 3000

    def test_software_maps_to_4000(self) -> None:
        assert _CATEGORY_MAP["software"] == 4000

    def test_games_maps_to_1000(self) -> None:
        assert _CATEGORY_MAP["games"] == 1000

    def test_other_maps_to_7020(self) -> None:
        assert _CATEGORY_MAP["other"] == 7020

    def test_reverse_mapping_exists(self) -> None:
        assert "movie" in _TORZNAB_TO_TYPES[2000]
        assert "tv" in _TORZNAB_TO_TYPES[5000]


# -- WarezomenPlugin.search() tests -----------------------------------------


def _mock_response(html: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response with given HTML content."""
    return httpx.Response(
        status_code=status_code,
        text=html,
        request=httpx.Request("GET", "https://warezomen.com/test"),
    )


class TestWarezomenSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("avatar")

        assert len(results) == 3
        assert all(isinstance(r, SearchResult) for r in results)

    async def test_search_result_fields(self) -> None:
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("avatar")

        first = results[0]
        assert first.title == "Avatar.2009.1080p.BluRay"
        assert first.download_link == "https://apps4all.com/dl/avatar"
        assert first.category == 2000
        assert first.published_date == "01-Nov-2025"
        assert first.download_links == [{"hoster": "apps4all", "link": "https://apps4all.com/dl/avatar"}]
        assert first.source_url == "https://warezomen.com/download/avatar/"

    async def test_search_empty_query(self) -> None:
        plugin = _WarezomenPlugin()
        results = await plugin.search("")
        assert results == []

    async def test_search_no_results(self) -> None:
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_NO_TABLE_HTML))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("nonexistent")

        assert results == []

    async def test_search_with_pagination(self) -> None:
        plugin = _WarezomenPlugin()

        responses = [
            _mock_response(_PAGINATION_HTML),
            _mock_response(_PAGE2_HTML),
        ]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=responses)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("test")

        assert len(results) == 2
        assert results[0].title == "Result.One"
        assert results[1].title == "Result.Two"
        assert mock_client.get.call_count == 2

    async def test_search_category_filter(self) -> None:
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("avatar", category=2000)

        # Only "Movie" type results (1 out of 3)
        assert len(results) == 1
        assert results[0].title == "Avatar.2009.1080p.BluRay"
        assert results[0].category == 2000

    async def test_search_category_filter_tv(self) -> None:
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("breaking bad", category=5000)

        assert len(results) == 1
        assert results[0].title == "Breaking.Bad.S01.720p"

    async def test_search_http_error_returns_empty(self) -> None:
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "https://warezomen.com/test"),
                response=httpx.Response(404),
            )
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("nonexistent")

        assert results == []

    async def test_search_unknown_type_gets_other_category(self) -> None:
        html = """
        <table class="download"><tbody>
        <tr>
          <td class="n"><a title="Something Weird" href="https://x.com/dl">X</a></td>
          <td class="n">hoster</td>
          <td class="t2">Unknown</td>
          <td>01-Jan-2025</td>
        </tr>
        </tbody></table>
        """
        plugin = _WarezomenPlugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(html))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await plugin.search("something")

        assert len(results) == 1
        assert results[0].category == 7020


class TestPluginProtocol:
    def test_module_exports_plugin_instance(self) -> None:
        assert hasattr(_warez, "plugin")
        assert _warez.plugin.name == "warezomen"

    def test_plugin_has_search_method(self) -> None:
        assert callable(getattr(_warez.plugin, "search", None))
