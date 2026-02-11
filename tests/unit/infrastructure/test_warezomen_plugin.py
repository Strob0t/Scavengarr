"""Tests for the warezomen.com YAML plugin (Scrapy-based)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from diskcache import Cache

from scavengarr.infrastructure.plugins.loader import load_yaml_plugin
from scavengarr.infrastructure.scraping.scrapy_adapter import ScrapyAdapter

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "warezomen.yaml"


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


# -- Plugin loading -----------------------------------------------------------


class TestPluginLoading:
    def test_yaml_loads_successfully(self) -> None:
        plugin = load_yaml_plugin(_PLUGIN_PATH)
        assert plugin.name == "warezomen"
        assert plugin.version == "1.0.0"
        assert "warezomen.com" in plugin.base_url

    def test_has_search_results_stage(self) -> None:
        plugin = load_yaml_plugin(_PLUGIN_PATH)
        assert plugin.scraping.stages is not None
        assert len(plugin.scraping.stages) == 1
        assert plugin.scraping.stages[0].name == "search_results"

    def test_stage_has_query_transform(self) -> None:
        plugin = load_yaml_plugin(_PLUGIN_PATH)
        assert plugin.scraping.stages is not None
        stage = plugin.scraping.stages[0]
        assert stage.query_transform == "slugify"

    def test_stage_has_rows_selector(self) -> None:
        plugin = load_yaml_plugin(_PLUGIN_PATH)
        assert plugin.scraping.stages is not None
        stage = plugin.scraping.stages[0]
        assert stage.selectors.rows == "table.download tbody tr"

    def test_stage_has_field_attributes(self) -> None:
        plugin = load_yaml_plugin(_PLUGIN_PATH)
        assert plugin.scraping.stages is not None
        stage = plugin.scraping.stages[0]
        assert stage.field_attributes == {
            "title": ["title"],
            "download_link": ["href"],
        }

    def test_pagination_configured(self) -> None:
        plugin = load_yaml_plugin(_PLUGIN_PATH)
        assert plugin.scraping.stages is not None
        stage = plugin.scraping.stages[0]
        assert stage.pagination is not None
        assert stage.pagination.enabled is True
        assert stage.pagination.max_pages == 50


# -- Helpers for ScrapyAdapter tests ------------------------------------------


def _mock_response(html: str, status_code: int = 200) -> httpx.Response:
    """Create a real httpx.Response with given HTML content."""
    return httpx.Response(
        status_code=status_code,
        content=html.encode("utf-8"),
        request=httpx.Request("GET", "https://warezomen.com/test"),
    )


def _make_adapter() -> tuple[ScrapyAdapter, AsyncMock]:
    """Create a ScrapyAdapter with the warezomen plugin and a mock client."""
    plugin = load_yaml_plugin(_PLUGIN_PATH)
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    cache = MagicMock(spec=Cache)
    adapter = ScrapyAdapter(
        plugin=plugin,
        http_client=mock_client,
        cache=cache,
        delay_seconds=0,
        max_retries=1,
    )
    return adapter, mock_client


# -- URL construction ---------------------------------------------------------


class TestUrlConstruction:
    def test_slugified_url(self) -> None:
        adapter, _ = _make_adapter()
        stage = adapter.stages["search_results"]
        url = stage.build_url(query="Avatar 2009")
        assert url == "https://warezomen.com/download/avatar-2009/"

    def test_special_chars_in_query(self) -> None:
        adapter, _ = _make_adapter()
        stage = adapter.stages["search_results"]
        url = stage.build_url(query="Spider-Man: No Way Home")
        assert url == "https://warezomen.com/download/spider-man-no-way-home/"


# -- Scraping results ---------------------------------------------------------


class TestScrapingResults:
    @pytest.mark.asyncio
    async def test_extracts_multiple_rows(self) -> None:
        adapter, mock_client = _make_adapter()
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))

        results = await adapter.scrape("avatar")

        # search_results stage should have items
        assert "search_results" in results
        items = results["search_results"]

        # Separator rows (td.d) should be filtered out (no title/link)
        titles = [item["title"] for item in items]
        assert "Avatar.2009.1080p.BluRay" in titles
        assert "Breaking.Bad.S01.720p" in titles
        assert "Photoshop.2024.Portable" in titles

    @pytest.mark.asyncio
    async def test_result_fields(self) -> None:
        adapter, mock_client = _make_adapter()
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))

        results = await adapter.scrape("avatar")
        items = results["search_results"]

        # Find the Avatar row
        avatar = next(i for i in items if "Avatar" in i.get("title", ""))
        assert avatar["title"] == "Avatar.2009.1080p.BluRay"
        assert avatar["download_link"] == "https://apps4all.com/dl/avatar"
        assert avatar["published_date"] == "01-Nov-2025"

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        adapter, mock_client = _make_adapter()
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_TABLE_HTML))

        results = await adapter.scrape("nonexistent")

        # No valid rows -> empty
        assert results == {} or all(len(v) == 0 for v in results.values())

    @pytest.mark.asyncio
    async def test_no_table_returns_empty(self) -> None:
        adapter, mock_client = _make_adapter()
        mock_client.get = AsyncMock(return_value=_mock_response(_NO_TABLE_HTML))

        results = await adapter.scrape("nonexistent")
        assert results == {} or all(len(v) == 0 for v in results.values())


# -- Pagination ---------------------------------------------------------------


class TestPagination:
    @pytest.mark.asyncio
    async def test_follows_next_page(self) -> None:
        adapter, mock_client = _make_adapter()
        responses = [
            _mock_response(_PAGINATION_HTML),
            _mock_response(_PAGE2_HTML),
        ]
        mock_client.get = AsyncMock(side_effect=responses)

        results = await adapter.scrape("test")
        items = results.get("search_results", [])

        titles = [item["title"] for item in items]
        assert "Result.One" in titles
        assert "Result.Two" in titles
        assert mock_client.get.call_count == 2


# -- Normalize results --------------------------------------------------------


class TestNormalizeResults:
    @pytest.mark.asyncio
    async def test_normalize_to_search_results(self) -> None:
        adapter, mock_client = _make_adapter()
        mock_client.get = AsyncMock(return_value=_mock_response(_RESULT_TABLE_HTML))

        raw = await adapter.scrape("avatar")
        search_results = adapter.normalize_results(raw)

        assert len(search_results) >= 3
        first = search_results[0]
        assert first.title == "Avatar.2009.1080p.BluRay"
        assert first.download_link == "https://apps4all.com/dl/avatar"
