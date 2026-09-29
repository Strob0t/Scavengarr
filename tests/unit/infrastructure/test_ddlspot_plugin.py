"""Tests for the ddlspot.com Python plugin (Playwright + httpx)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "ddlspot.py"
_PW_PATCH = "scavengarr.infrastructure.plugins.playwright_base.async_playwright"


def _load_ddlspot_module() -> ModuleType:
    """Load ddlspot.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("ddlspot_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_ddlspot = _load_ddlspot_module()
_DDLSpotPlugin = _ddlspot.DDLSpotPlugin
_SearchResultParser = _ddlspot._SearchResultParser
_DetailPageParser = _ddlspot._DetailPageParser
_hoster_from_url = _ddlspot._hoster_from_url
_CATEGORY_MAP = _ddlspot._CATEGORY_MAP
_REVERSE_CATEGORY_MAP = _ddlspot._REVERSE_CATEGORY_MAP


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_THEAD = (  # noqa: E501
    "<thead><tr class='headings'>"
    "<th>Name</th><th>Age</th><th>Type</th>"
    "<th>Size</th><th>Links</th>"
    "</tr></thead>"
)

_SEARCH_HTML = (
    "<html><body>"
    "<table class='download'>"
    f"{_THEAD}"
    "<tbody>"
    "  <tr class='row'>"
    "    <td class='c'>"
    '    <a href="/file/123/iron-man-2008/">'
    "Iron Man 2008 1080p</a></td>"
    "    <td>2 days</td><td>Movies</td>"
    "    <td>4.50 GB</td><td>1</td>"
    "  </tr>"
    "  <tr><td colspan='5' class='links'>"
    "  /abc.Iron.Man.2008.rar<br>"
    "  <strong>Links: Rapidgator</strong></td></tr>"
    "  <tr class='row'>"
    "    <td class='c'>"
    '    <a href="/file/456/ubuntu-24/">'
    "Ubuntu 24.04 LTS</a></td>"
    "    <td>5 days</td><td>Software</td>"
    "    <td>2.10 GB</td><td>2</td>"
    "  </tr>"
    "  <tr><td colspan='5' class='links'>"
    "  /ubuntu-24.04.iso<br>"
    "  <strong>Links: Alfafile</strong></td></tr>"
    "</tbody></table></body></html>"
)

_SEARCH_HTML_SINGLE = (
    "<html><body>"
    "<table class='download'>"
    f"{_THEAD}"
    "<tbody>"
    "  <tr class='row'>"
    "    <td class='c'>"
    '    <a href="/file/789/game-title/">'
    "Game Title 2025</a></td>"
    "    <td>1 day</td><td>Games</td>"
    "    <td>30 GB</td><td>3</td>"
    "  </tr>"
    "  <tr><td colspan='5' class='links'>"
    "  game.rar<br>"
    "  <strong>Links: Rapidgator</strong></td></tr>"
    "</tbody></table></body></html>"
)

_SEARCH_HTML_EMPTY = (
    f"<html><body><table class='download'>{_THEAD}<tbody></tbody></table></body></html>"
)

_SEARCH_HTML_NO_TABLE = "<html><body><p>No results found.</p></body></html>"

_DETAIL_HTML_SINGLE = """
<html><body>
<span class="headings">Iron Man 2008 1080p</span>
<div class="links-box">https://rapidgator.net/file/abc/iron.man.rar.html<br></div>
<div class="footer">Size: <strong>4.50 GB</strong></div>
</body></html>
"""

_DETAIL_HTML_MULTI = """
<html><body>
<span class="headings">Ubuntu 24.04 LTS</span>
<div class="links-box">
https://rapidgator.net/file/abc/ubuntu.iso.html
https://alfafile.net/file/def/ubuntu.iso
https://nitroflare.com/view/ghi/ubuntu.iso
</div>
</body></html>
"""

_DETAIL_HTML_EMPTY = """
<html><body>
<span class="headings">Empty Page</span>
<div class="links-box"></div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Mock helpers (same pattern as boerse tests)
# ---------------------------------------------------------------------------


def _make_mock_page(
    content: str = "<html></html>",
) -> AsyncMock:
    """Create a mock Playwright Page."""
    page = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status = 200
    page.goto = AsyncMock(return_value=mock_response)
    page.wait_for_function = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value=content)
    page.close = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    return page


def _make_mock_context(
    pages: list[AsyncMock] | None = None,
) -> AsyncMock:
    """Create a mock BrowserContext that yields pages in order."""
    context = AsyncMock()
    if pages:
        context.new_page = AsyncMock(side_effect=pages)
    else:
        context.new_page = AsyncMock(return_value=_make_mock_page())
    context.close = AsyncMock()
    return context


def _make_mock_browser(context: AsyncMock | None = None) -> AsyncMock:
    """Create a mock Browser."""
    browser = AsyncMock()
    browser.is_connected = MagicMock(return_value=True)
    browser.new_context = AsyncMock(return_value=context or _make_mock_context())
    browser.close = AsyncMock()
    return browser


def _make_mock_playwright(browser: AsyncMock | None = None) -> AsyncMock:
    """Create a mock Playwright instance."""
    pw = AsyncMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser or _make_mock_browser())
    pw.stop = AsyncMock()
    return pw


def _make_plugin() -> object:
    """Create a DDLSpotPlugin instance."""
    return _DDLSpotPlugin()


def _mock_details(plugin: object, pages: dict[str, str]) -> None:
    """Serve detail-page HTML by URL path ("" = fetch failed)."""

    async def _fetch(url: str) -> str:
        return pages.get(urlparse(url).path, "")

    plugin._fetch_detail_page = _fetch  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests: Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        p = _make_plugin()
        assert p.name == "ddlspot"

    def test_version(self) -> None:
        p = _make_plugin()
        assert p.version == "1.0.0"

    def test_mode(self) -> None:
        p = _make_plugin()
        assert p.mode == "playwright"

    def test_module_exports_plugin(self) -> None:
        assert hasattr(_ddlspot, "plugin")
        assert _ddlspot.plugin.name == "ddlspot"


# ---------------------------------------------------------------------------
# Tests: SearchResultParser
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    def test_parse_two_results(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        r1 = parser.results[0]
        assert r1["title"] == "Iron Man 2008 1080p"
        assert r1["detail_url"] == "/file/123/iron-man-2008/"
        assert r1["size"] == "4.50 GB"
        assert r1["type_str"] == "Movies"

        r2 = parser.results[1]
        assert r2["title"] == "Ubuntu 24.04 LTS"
        assert r2["detail_url"] == "/file/456/ubuntu-24/"
        assert r2["size"] == "2.10 GB"
        assert r2["type_str"] == "Software"

    def test_parse_single_result(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML_SINGLE)

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Game Title 2025"
        assert parser.results[0]["type_str"] == "Games"

    def test_parse_empty_table(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML_EMPTY)
        assert parser.results == []

    def test_parse_no_table(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML_NO_TABLE)
        assert parser.results == []

    def test_parse_ignores_non_download_tables(self) -> None:
        html = """
        <table class="other"><tbody>
        <tr class="row"><td class="c"><a href="/x">X</a></td>
        <td>1</td><td>Y</td><td>1 GB</td><td>1</td></tr>
        <tr><td colspan="5" class="links">x</td></tr>
        </tbody></table>
        """
        parser = _SearchResultParser()
        parser.feed(html)
        assert parser.results == []


# ---------------------------------------------------------------------------
# Tests: DetailPageParser
# ---------------------------------------------------------------------------


class TestSearchResultTitles:
    def test_prefers_full_title_attribute(self) -> None:
        html = (
            "<table class='download'><tbody><tr class='row'><td class='c'>"
            '<a href="/file/1/x/" title="Iron Man 2008 IMAX 2160p-PSA | Rapidgator">'
            "<b>Iron</b> <b>Man</b> 2008 IMAX 21..</a></td><td>1 day</td>"
            "<td>Movies</td><td>9 GB</td><td>1</td></tr>"
            "<tr><td colspan='5' class='links'>x</td></tr></tbody></table>"
        )
        parser = _ddlspot._SearchResultParser()
        parser.feed(html)
        assert parser.results[0]["title"] == "Iron Man 2008 IMAX 2160p-PSA"

    def test_bold_chunks_keep_spaces_without_title_attribute(self) -> None:
        html = (
            "<table class='download'><tbody><tr class='row'><td class='c'>"
            '<a href="/file/1/x/"><b>Iron</b> <b>Man</b> 2008</a></td>'
            "<td>1 day</td><td>Movies</td><td>9 GB</td><td>1</td></tr>"
            "<tr><td colspan='5' class='links'>x</td></tr></tbody></table>"
        )
        parser = _ddlspot._SearchResultParser()
        parser.feed(html)
        assert parser.results[0]["title"] == "Iron Man 2008"


class TestFetchDetailPage:
    async def test_returns_html_via_browser(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page("<div class='links-box'>https://a/b</div>")
        plugin._context = _make_mock_context(pages=[page])
        plugin._browser = _make_mock_browser(plugin._context)

        html = await plugin._fetch_detail_page("https://www.ddlspot.com/file/1/x/")

        assert "links-box" in html
        page.close.assert_awaited_once()

    async def test_navigation_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_FAILED"))
        plugin._context = _make_mock_context(pages=[page])
        plugin._browser = _make_mock_browser(plugin._context)

        assert await plugin._fetch_detail_page("https://www.ddlspot.com/f/") == ""
        page.close.assert_awaited_once()


class TestDetailPageParser:
    def test_parse_single_url(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML_SINGLE)

        assert len(parser.urls) == 1
        assert parser.urls[0] == "https://rapidgator.net/file/abc/iron.man.rar.html"

    def test_parse_multiple_urls(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML_MULTI)

        assert len(parser.urls) == 3
        assert "rapidgator.net" in parser.urls[0]
        assert "alfafile.net" in parser.urls[1]
        assert "nitroflare.com" in parser.urls[2]

    def test_parse_empty_links_box(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML_EMPTY)
        assert parser.urls == []

    def test_parse_no_links_box(self) -> None:
        parser = _DetailPageParser()
        parser.feed("<html><body>No links box</body></html>")
        assert parser.urls == []

    def test_non_http_lines_ignored(self) -> None:
        html = """
        <div class="links-box">
        Some description text
        ftp://not-http.com/file
        https://valid.com/file.rar
        just random text
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.urls) == 1
        assert parser.urls[0] == "https://valid.com/file.rar"

    def test_nested_divs_dont_exit_early(self) -> None:
        html = """
        <div class="links-box">
          <div class="inner">nested content</div>
          https://example.com/after-nested.rar
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.urls) == 1
        assert parser.urls[0] == "https://example.com/after-nested.rar"


# ---------------------------------------------------------------------------
# Tests: _hoster_from_url
# ---------------------------------------------------------------------------


class TestHosterFromUrl:
    def test_rapidgator(self) -> None:
        assert _hoster_from_url("https://rapidgator.net/file/abc") == "rapidgator"

    def test_www_prefix_stripped(self) -> None:
        assert _hoster_from_url("https://www.alfafile.net/file/x") == "alfafile"

    def test_invalid_url(self) -> None:
        assert _hoster_from_url("not-a-url") == "unknown"


# ---------------------------------------------------------------------------
# Tests: Category mapping
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    def test_movies_maps_to_2000(self) -> None:
        assert _CATEGORY_MAP["movies"] == 2000

    def test_tv_maps_to_5000(self) -> None:
        assert _CATEGORY_MAP["tv"] == 5000

    def test_software_maps_to_4000(self) -> None:
        assert _CATEGORY_MAP["software"] == 4000

    def test_games_maps_to_4000(self) -> None:
        assert _CATEGORY_MAP["games"] == 4000

    def test_ebooks_maps_to_7000(self) -> None:
        assert _CATEGORY_MAP["e-books"] == 7000

    def test_reverse_map_movies(self) -> None:
        assert _REVERSE_CATEGORY_MAP[2000] == "movies"

    def test_reverse_map_tv(self) -> None:
        assert _REVERSE_CATEGORY_MAP[5000] == "tv"


# ---------------------------------------------------------------------------
# Tests: Cloudflare wait
# ---------------------------------------------------------------------------


class TestCloudflareWait:
    async def test_wait_passes_when_no_challenge(self) -> None:
        p = _make_plugin()
        page = _make_mock_page()
        assert await p._wait_for_cloudflare(page) is True

    async def test_timeout_does_not_raise(self) -> None:
        p = _make_plugin()
        page = _make_mock_page()
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("timeout"))
        await p._wait_for_cloudflare(page)


# ---------------------------------------------------------------------------
# Tests: Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    async def test_cleanup_closes_resources(self) -> None:
        p = _make_plugin()

        context = _make_mock_context()
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        p._pw = pw
        p._browser = browser
        p._context = context

        await p.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert p._context is None
        assert p._browser is None
        assert p._pw is None

    async def test_cleanup_when_not_started(self) -> None:
        p = _make_plugin()
        await p.cleanup()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: Full search flow
# ---------------------------------------------------------------------------


class TestSearchResultParserPagination:
    def test_next_page_url_extracted(self) -> None:
        html = (
            _SEARCH_HTML
            + '<div class="box-content">[ 1 ] &nbsp; '
            + '<a href="/search/2/?q=test&m=1">Next Page &raquo;</a></div>'
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert parser.next_page_url == "/search/2/?q=test&m=1"
        assert len(parser.results) == 2

    def test_no_next_page_url(self) -> None:
        parser = _SearchResultParser()
        parser.feed(_SEARCH_HTML)

        assert parser.next_page_url == ""


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        _mock_details(
            plugin,
            {
                "/file/123/iron-man-2008/": _DETAIL_HTML_SINGLE,
                "/file/456/ubuntu-24/": _DETAIL_HTML_MULTI,
            },
        )

        # Mock detail page responses

        # Set up Playwright mocks (search page + empty page 2)
        search_page = _make_mock_page(_SEARCH_HTML)
        empty_page = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_page, empty_page])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with patch(_PW_PATCH) as mock_ap:
            mock_ap.return_value.start = mock_start
            results = await plugin.search("iron man")

        assert len(results) == 2

        # First result: Iron Man (Movies)
        r1 = results[0]
        assert r1.title == "Iron Man 2008 1080p"
        assert "rapidgator.net" in r1.download_link
        assert r1.size == "4.50 GB"
        assert r1.category == 2000
        assert len(r1.download_links) == 1

        # Second result: Ubuntu (Software)
        r2 = results[1]
        assert r2.title == "Ubuntu 24.04 LTS"
        assert "rapidgator.net" in r2.download_link
        assert r2.size == "2.10 GB"
        assert r2.category == 4000
        assert len(r2.download_links) == 3

    async def test_search_page_failure_keeps_collected_rows(self) -> None:
        """A timeout on page 2 must not discard page 1."""
        from patchright.async_api import TimeoutError as PlaywrightTimeoutError

        plugin = _make_plugin()
        _mock_details(
            plugin,
            {
                "/file/123/iron-man-2008/": _DETAIL_HTML_SINGLE,
                "/file/456/ubuntu-24/": _DETAIL_HTML_MULTI,
            },
        )
        page_1 = _make_mock_page(
            _SEARCH_HTML
            + '<div class="box-content">[ 1 ] &nbsp; '
            + '<a href="/search/2/?q=iron+man&m=1">Next Page &raquo;</a></div>'
        )
        page_2 = _make_mock_page()
        page_2.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout"))
        context = _make_mock_context(pages=[page_1, page_2])
        pw = _make_mock_playwright(_make_mock_browser(context))

        with patch(_PW_PATCH) as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=pw)
            results = await plugin.search("iron man")

        assert [r.title for r in results] == ["Iron Man 2008 1080p", "Ubuntu 24.04 LTS"]

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_page])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with patch(_PW_PATCH) as mock_ap:
            mock_ap.return_value.start = mock_start
            results = await plugin.search("nonexistent")

        assert results == []

    async def test_search_with_category_filter(self) -> None:
        plugin = _make_plugin()
        _mock_details(plugin, {"/file/123/iron-man-2008/": _DETAIL_HTML_SINGLE})

        # Only the Movies detail page should be fetched

        search_page = _make_mock_page(_SEARCH_HTML)
        empty_page = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_page, empty_page])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with patch(_PW_PATCH) as mock_ap:
            mock_ap.return_value.start = mock_start
            results = await plugin.search("iron man", category=2000)

        # Only Movies result should be returned
        assert len(results) == 1
        assert results[0].title == "Iron Man 2008 1080p"
        assert results[0].category == 2000

    async def test_search_detail_page_failure_skips_result(self) -> None:
        plugin = _make_plugin()
        _mock_details(
            plugin,
            {
                "/file/123/iron-man-2008/": "",
                "/file/456/ubuntu-24/": _DETAIL_HTML_MULTI,
            },
        )

        # Detail page returns 500

        search_page = _make_mock_page(_SEARCH_HTML)
        empty_page = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_page, empty_page])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with patch(_PW_PATCH) as mock_ap:
            mock_ap.return_value.start = mock_start
            results = await plugin.search("iron man")

        # Only Ubuntu should succeed (Iron Man detail page failed)
        assert len(results) == 1
        assert results[0].title == "Ubuntu 24.04 LTS"

    async def test_search_detail_page_empty_links_skips_result(self) -> None:
        plugin = _make_plugin()
        _mock_details(
            plugin,
            {
                "/file/123/iron-man-2008/": _DETAIL_HTML_EMPTY,
                "/file/456/ubuntu-24/": _DETAIL_HTML_MULTI,
            },
        )

        search_page = _make_mock_page(_SEARCH_HTML)
        empty_page = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_page, empty_page])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with patch(_PW_PATCH) as mock_ap:
            mock_ap.return_value.start = mock_start
            results = await plugin.search("iron man")

        assert len(results) == 1
        assert results[0].title == "Ubuntu 24.04 LTS"

    async def test_search_reuses_browser(self) -> None:
        """Second search call reuses the existing browser context."""
        plugin = _make_plugin()

        # Set up existing browser
        search_page1 = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        search_page2 = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_page1, search_page2])
        browser = _make_mock_browser(context)

        plugin._browser = browser
        plugin._context = context

        await plugin.search("test1")
        await plugin.search("test2")

        # new_page should have been called twice (one per search)
        assert context.new_page.await_count == 2

    async def test_search_paginates(self) -> None:
        """Verify pagination follows Next Page links."""
        plugin = _make_plugin()
        _mock_details(
            plugin,
            {
                "/file/123/iron-man-2008/": _DETAIL_HTML_SINGLE,
                "/file/456/ubuntu-24/": _DETAIL_HTML_MULTI,
                "/file/789/game-title/": _DETAIL_HTML_SINGLE,
            },
        )

        # Page 1: has results + "Next Page" link
        page1_html = (
            _SEARCH_HTML
            + '<div class="box-content">'
            + '<a href="/search/2/?q=test&m=1">Next Page &raquo;</a>'
            + "</div>"
        )
        # Page 2: has one result, no next page
        page2_html = _SEARCH_HTML_SINGLE

        # Mock detail pages for all 3 results

        search_p1 = _make_mock_page(page1_html)
        search_p2 = _make_mock_page(page2_html)
        empty_page = _make_mock_page(_SEARCH_HTML_NO_TABLE)
        context = _make_mock_context(pages=[search_p1, search_p2, empty_page])
        browser = _make_mock_browser(context)

        plugin._browser = browser
        plugin._context = context

        results = await plugin.search("test")

        # 2 from page 1 + 1 from page 2
        assert len(results) == 3
