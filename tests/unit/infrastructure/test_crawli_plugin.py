"""Tests for the crawli.net Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "crawli.py"


def _load_crawli_module() -> ModuleType:
    """Load crawli.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("crawli_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_crawli = _load_crawli_module()
_CrawliPlugin = _crawli.CrawliPlugin
_SearchResultParser = _crawli._SearchResultParser


def _make_plugin() -> object:
    """Create CrawliPlugin instance."""
    return _CrawliPlugin()


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------
_SEARCH_PAGE_HTML = """
<html><body>
<div id="dpage" class="hfeed">
<div class="hentry">

<h1 class="entry-title">Download &amp; Stream &gt; Batman</h1>
<abbr title="2026-02-12T23:00:38+01:00" class="updated">12.02.2026</abbr>

    <div class="entry-content sresd">
    <strong class="sres"><a href="http://crawli.net/go/?/6781604/"
        target="_blank" rel="nofollow" class="sres3">Batman und Harley Quinn (2017)</a></strong>
    <div style="float:right"><em class="fnfo">Download</em></div>
    <div class="scont">
    <p>Release: Batman.und.Harley.Quinn.2017.German.AC3.DL.1080p.BluRay.x265-FuN</p>
    <address class="resl author">funxd.site/2026/01/29/batman-und-harley-quinn-2017/</address>
    <small class="rtime published">29.01.2026 19:35</small>
    </div>
    </div>

    <div class="entry-content sresd">
    <strong class="sres"><a href="http://crawli.net/go/?/6778536/"
        target="_blank" rel="nofollow" class="sres3">Batman.1989.GERMAN.DL.HDR.2160.WEB.H265-SunDry</a></strong>
    <div style="float:right"><em class="fnfo">Download</em></div>
    <div class="scont">
    <p></p>
    <address class="resl author">warez-ddl.to/download/406987/Batman.1989</address>
    <small class="rtime published">22.01.2026 04:00</small>
    </div>
    </div>

    <div class="entry-content sresd">
    <strong class="sres"><a href="http://crawli.net/go/?/6778544/"
        target="_blank" rel="nofollow" class="sres3">Batman.Forever.1995.GERMAN.DL.HDR.2160P.WEB.H265-SunDry</a></strong>
    <div style="float:right"><em class="fnfo">Download</em></div>
    <div class="scont">
    <p>Some description here</p>
    <address class="resl author">https://example.com/download/batman-forever</address>
    <small class="rtime published">22.01.2026 04:00</small>
    </div>
    </div>

</div>
</div>

<div id="foot">
<span class="pages">
<a href="//crawli.net/film/Batman/" title="Batman">1</a>
<a href="//crawli.net/film/Batman/p-2/">2</a>
<a href="//crawli.net/film/Batman/p-3/">3</a>
<a href="//crawli.net/film/Batman/p-4/">4</a>
<a href="//crawli.net/film/Batman/p-5/">5</a>
</span>
</div>

</body></html>
"""

_EMPTY_HTML = """
<html><body>
<div id="dpage" class="hfeed">
<div class="hentry">
<h1 class="entry-title">Download &amp; Stream &gt; xyznonexistent</h1>
</div>
</div>
<div id="foot"></div>
</body></html>
"""

_NO_ADDRESS_HTML = """
<html><body>
<div id="dpage" class="hfeed">
<div class="hentry">
    <div class="entry-content sresd">
    <strong class="sres"><a href="http://crawli.net/go/?/999/"
        target="_blank" rel="nofollow" class="sres3">No Source URL Result</a></strong>
    <div style="float:right"><em class="fnfo">Download</em></div>
    <div class="scont">
    <p>Description text</p>
    </div>
    </div>
</div>
</div>
<div id="foot"></div>
</body></html>
"""

_SINGLE_RESULT_HTML = """
<html><body>
<div id="dpage" class="hfeed">
<div class="hentry">
    <div class="entry-content sresd">
    <strong class="sres"><a href="http://crawli.net/go/?/1234/"
        target="_blank" rel="nofollow" class="sres3">Test.Result.2025.German.1080p</a></strong>
    <div style="float:right"><em class="fnfo">Download</em></div>
    <div class="scont">
    <p>Test description</p>
    <address class="resl author">example.com/test-download</address>
    <small class="rtime published">15.06.2025 12:00</small>
    </div>
    </div>
</div>
</div>
<div id="foot"></div>
</body></html>
"""

_PAGE2_HTML = """
<html><body>
<div id="dpage" class="hfeed">
<div class="hentry">
    <div class="entry-content sresd">
    <strong class="sres"><a href="http://crawli.net/go/?/5555/"
        target="_blank" rel="nofollow" class="sres3">Page2.Result.2025</a></strong>
    <div style="float:right"><em class="fnfo">Download</em></div>
    <div class="scont">
    <p></p>
    <address class="resl author">other-site.org/page2-result</address>
    <small class="rtime published">10.02.2026 08:00</small>
    </div>
    </div>
</div>
</div>
<div id="foot">
<span class="pages">
<a href="//crawli.net/all/test/p-2/">2</a>
<a href="//crawli.net/all/test/p-3/">3</a>
</span>
</div>
</body></html>
"""


# ===========================================================================
# Parser tests
# ===========================================================================
class TestSearchResultParser:
    """Unit tests for _SearchResultParser."""

    def test_parses_three_results(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        assert len(parser.results) == 3

    def test_extracts_title(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        assert parser.results[0]["title"] == "Batman und Harley Quinn (2017)"
        assert parser.results[1]["title"] == (
            "Batman.1989.GERMAN.DL.HDR.2160.WEB.H265-SunDry"
        )

    def test_extracts_source_url_with_scheme(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        # Source URL without scheme gets https:// prepended
        assert parser.results[0]["source_url"] == (
            "https://funxd.site/2026/01/29/batman-und-harley-quinn-2017/"
        )
        assert parser.results[1]["source_url"] == (
            "https://warez-ddl.to/download/406987/Batman.1989"
        )

    def test_preserves_existing_scheme(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        # Third result already has https:// scheme
        assert parser.results[2]["source_url"] == (
            "https://example.com/download/batman-forever"
        )

    def test_extracts_date(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        assert parser.results[0]["date"] == "29.01.2026 19:35"
        assert parser.results[1]["date"] == "22.01.2026 04:00"

    def test_extracts_description(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        assert "Batman.und.Harley.Quinn.2017" in parser.results[0]["description"]
        assert parser.results[2]["description"] == "Some description here"

    def test_detects_max_page(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_PAGE_HTML)
        assert parser.max_page == 5

    def test_empty_results(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_EMPTY_HTML)
        assert len(parser.results) == 0
        assert parser.max_page == 1

    def test_skips_result_without_address(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_NO_ADDRESS_HTML)
        assert len(parser.results) == 0

    def test_single_result_no_pagination(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SINGLE_RESULT_HTML)
        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Test.Result.2025.German.1080p"
        assert parser.results[0]["source_url"] == "https://example.com/test-download"
        assert parser.results[0]["date"] == "15.06.2025 12:00"
        assert parser.max_page == 1


# ===========================================================================
# Plugin integration tests
# ===========================================================================
def _mock_response(html: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=html,
        request=httpx.Request("GET", "https://crawli.net/test"),
    )


class TestCrawliPlugin:
    """Integration tests for CrawliPlugin."""

    def test_plugin_attributes(self) -> None:
        p = _make_plugin()
        assert p.name == "crawli"
        assert p.provides == "download"
        assert p.default_language == "de"
        assert "crawli.net" in p._domains

    def test_module_exports_plugin(self) -> None:
        assert hasattr(_crawli, "plugin")
        assert _crawli.plugin.name == "crawli"

    @pytest.mark.asyncio
    async def test_search_basic(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_PAGE_HTML),  # page 1
                _mock_response(_EMPTY_HTML),  # page 2 -> stop
            ]
        )
        p._client = mock_client

        results = await p.search("Batman", category=2000)

        assert len(results) == 3
        assert results[0].title == "Batman und Harley Quinn (2017)"
        assert results[0].download_link == (
            "https://funxd.site/2026/01/29/batman-und-harley-quinn-2017/"
        )
        assert results[0].category == 2000

    @pytest.mark.asyncio
    async def test_search_uses_film_category_path(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_SINGLE_RESULT_HTML))
        p._client = mock_client

        await p.search("Batman", category=2000)

        # First call should use /film/ path
        call_url = mock_client.get.call_args_list[0][0][0]
        assert "/film/" in call_url

    @pytest.mark.asyncio
    async def test_search_uses_serie_category_path(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_SINGLE_RESULT_HTML))
        p._client = mock_client

        await p.search("Breaking Bad", category=5000)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert "/serie/" in call_url

    @pytest.mark.asyncio
    async def test_search_uses_all_without_category(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_SINGLE_RESULT_HTML))
        p._client = mock_client

        await p.search("test query")

        call_url = mock_client.get.call_args_list[0][0][0]
        assert "/all/" in call_url

    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_HTML))
        p._client = mock_client

        results = await p.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_paginates(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        # Page 1 returns results with pagination, page 2 returns more
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_PAGE_HTML),  # page 1 (3 results, max_page=5)
                _mock_response(_PAGE2_HTML),  # page 2 (1 result)
                _mock_response(_EMPTY_HTML),  # page 3 (empty -> stop)
            ]
        )
        p._client = mock_client

        results = await p.search("Batman")

        # 3 from page 1 + 1 from page 2 = 4
        assert len(results) == 4
        assert mock_client.get.call_count == 3

    @pytest.mark.asyncio
    async def test_search_url_encodes_query(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_HTML))
        p._client = mock_client

        await p.search("Batman Begins 2005")

        call_url = mock_client.get.call_args_list[0][0][0]
        assert "Batman+Begins+2005" in call_url

    @pytest.mark.asyncio
    async def test_search_fetch_failure_returns_empty(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        p._client = mock_client

        results = await p.search("Batman")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_result_has_published_date(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_SEARCH_PAGE_HTML))
        p._client = mock_client

        results = await p.search("Batman")

        assert results[0].published_date == "29.01.2026 19:35"

    @pytest.mark.asyncio
    async def test_search_result_has_description(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_SEARCH_PAGE_HTML))
        p._client = mock_client

        results = await p.search("Batman")

        assert "Batman.und.Harley.Quinn.2017" in (results[0].description or "")

    @pytest.mark.asyncio
    async def test_search_uses_spiel_path(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_HTML))
        p._client = mock_client

        await p.search("Cyberpunk", category=4000)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert "/spiel/" in call_url

    @pytest.mark.asyncio
    async def test_search_uses_music_path(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_HTML))
        p._client = mock_client

        await p.search("Metallica", category=3000)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert "/music/" in call_url

    @pytest.mark.asyncio
    async def test_search_uses_apps_path(self) -> None:
        p = _make_plugin()
        p._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_HTML))
        p._client = mock_client

        await p.search("Photoshop", category=5020)

        call_url = mock_client.get.call_args_list[0][0][0]
        assert "/apps/" in call_url

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        p = _make_plugin()
        mock_client = AsyncMock()
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None
