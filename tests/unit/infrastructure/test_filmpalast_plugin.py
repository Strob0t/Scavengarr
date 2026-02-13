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
