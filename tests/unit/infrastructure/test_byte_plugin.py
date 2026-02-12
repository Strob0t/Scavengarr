"""Tests for the byte.to Python plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "byte.py"


def _load_module() -> ModuleType:
    """Load byte.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("byte_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_BytePlugin = _mod.BytePlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_IframeLinkParser = _mod._IframeLinkParser
_TORZNAB_TO_SITE_CATEGORY = _mod._TORZNAB_TO_SITE_CATEGORY
_SITE_CATEGORY_MAP = _mod._SITE_CATEGORY_MAP
_site_category_to_torznab = _mod._site_category_to_torznab


def _make_plugin() -> object:
    return _BytePlugin()


def _make_mock_page(content: str = "<html></html>") -> AsyncMock:
    page = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status = 200
    page.goto = AsyncMock(return_value=mock_response)
    page.wait_for_function = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value=content)
    page.close = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    # Default: no iframes
    main_frame = MagicMock()
    page.main_frame = main_frame
    page.frames = [main_frame]
    return page


def _make_mock_detail_page(
    main_html: str = "<html></html>",
    iframe_html: str = "",
) -> AsyncMock:
    """Create a mock page with optional iframe content."""
    page = _make_mock_page(main_html)
    if iframe_html:
        iframe = MagicMock()
        iframe.wait_for_load_state = AsyncMock()
        iframe.content = AsyncMock(return_value=iframe_html)
        page.frames = [page.main_frame, iframe]
    return page


def _make_mock_context(
    pages: list[AsyncMock] | None = None,
) -> AsyncMock:
    context = AsyncMock()
    if pages:
        context.new_page = AsyncMock(side_effect=pages)
    else:
        context.new_page = AsyncMock(return_value=_make_mock_page())
    context.close = AsyncMock()
    return context


def _make_mock_browser(
    context: AsyncMock | None = None,
) -> AsyncMock:
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context or _make_mock_context())
    browser.close = AsyncMock()
    return browser


def _make_mock_playwright(
    browser: AsyncMock | None = None,
) -> AsyncMock:
    pw = AsyncMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser or _make_mock_browser())
    pw.stop = AsyncMock()
    return pw


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

_SEARCH_HTML = """
<table class="SEARCH_ITEMLIST">
  <tr><th><h1>Suche nach: batman (42 Treffer)</h1></th></tr>
  <tr><td>
    <table class="NAVIGATION">
      <tr>
        <td><a href="/?q=batman&t=1&h=1&e=0&start=1">1</a></td>
        <td><a href="/?q=batman&t=1&h=1&e=0&start=2">2</a></td>
        <td><a href="/?q=batman&t=1&h=1&e=0&start=3">3</a></td>
      </tr>
    </table>
    <table>
      <tr>
        <td><p><b>Name</b></p></td>
        <td><p><b>Kategorie</b></p></td>
        <td><p><b>Datum</b></p></td>
      </tr>
      <tr>
        <td class="MOD">
          <p class="TITLE">
            <a href="/Filme/UHD-2160p/Batman-Forever-12345.html">
              Batman Forever
            </a>
          </p>
        </td>
        <td class="MOD"><p><a href="/?cat=80">UHD - 2160p</a></p></td>
        <td class="MOD"><p>21.01.26 22:38</p></td>
      </tr>
      <tr>
        <td class="MOD">
          <p class="TITLE">
            <a href="/Tv/Serien/Batman-S01-67890.html">Batman S01</a>
          </p>
        </td>
        <td class="MOD"><p><a href="/?cat=3">Serien</a></p></td>
        <td class="MOD"><p>20.01.26 18:00</p></td>
      </tr>
    </table>
  </td></tr>
</table>
"""

_SEARCH_SINGLE_HTML = """
<table class="SEARCH_ITEMLIST">
  <tr><th><h1>Suche nach: test (1 Treffer)</h1></th></tr>
  <tr><td>
    <table>
      <tr>
        <td class="MOD">
          <p class="TITLE">
            <a href="/Filme/HD-1080p/Test-Movie-111.html">Test Movie</a>
          </p>
        </td>
        <td class="MOD"><p><a href="/?cat=93">HD - 1080p</a></p></td>
        <td class="MOD"><p>15.02.26 12:00</p></td>
      </tr>
    </table>
  </td></tr>
</table>
"""

_DETAIL_HTML = """
<html><body>
<table>
  <tr><th colspan="2"><h1>Batman Forever</h1></th></tr>
</table>
<table>
  <tr>
    <td>Batman.Forever.1995.GERMAN.DL.HDR.2160P.WEB.H265-SunDry</td>
  </tr>
  <tr>
    <td>Kategorie:</td>
    <td>UHD - 2160p</td>
  </tr>
  <tr>
    <td>Größe:</td>
    <td>7.14 GB</td>
  </tr>
</table>
<table>
  <tr><th>Mirror #1 von uploader | Passwort: keine Angabe</th></tr>
  <tr><td><iframe src="https://byte.to/iframe/123"></iframe></td></tr>
</table>
</body></html>
"""

_IFRAME_HTML = """
<html><body>
  <img alt="rapidgator.net">
  <a href="https://hide.cx/container/uuid-111">Online rapidgator.net</a>
  <br>
  <img alt="ddownload.com">
  <a href="https://byte.to/go.php?hash=abc">Online ddownload.com</a>
</body></html>
"""


# ---------------------------------------------------------------------------
# SearchResultParser tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    def test_results_extracted(self) -> None:
        parser = _SearchResultParser("https://byte.to")
        parser.feed(_SEARCH_HTML)
        parser.flush_pending()

        assert len(parser.results) == 2
        assert parser.results[0]["title"] == "Batman Forever"
        assert parser.results[0]["url"] == (
            "https://byte.to/Filme/UHD-2160p/Batman-Forever-12345.html"
        )
        assert parser.results[0]["category"] == "UHD - 2160p"
        assert parser.results[1]["title"] == "Batman S01"
        assert parser.results[1]["category"] == "Serien"

    def test_total_hits_extracted(self) -> None:
        parser = _SearchResultParser("https://byte.to")
        parser.feed(_SEARCH_HTML)

        assert parser.total_hits == 42

    def test_max_page_extracted(self) -> None:
        parser = _SearchResultParser("https://byte.to")
        parser.feed(_SEARCH_HTML)

        assert parser.max_page == 3

    def test_no_navigation_defaults_to_1(self) -> None:
        parser = _SearchResultParser("https://byte.to")
        parser.feed(_SEARCH_SINGLE_HTML)

        assert parser.max_page == 1

    def test_empty_results(self) -> None:
        html = """
        <table class="SEARCH_ITEMLIST">
          <tr><th><h1>Suche nach: xyz (0 Treffer)</h1></th></tr>
        </table>
        """
        parser = _SearchResultParser("https://byte.to")
        parser.feed(html)
        parser.flush_pending()

        assert len(parser.results) == 0
        assert parser.total_hits == 0

    def test_header_row_ignored(self) -> None:
        """The column header row (Name/Kategorie/Datum) has no links."""
        parser = _SearchResultParser("https://byte.to")
        parser.feed(_SEARCH_HTML)
        parser.flush_pending()

        # Should only have data rows, not header
        assert len(parser.results) == 2

    def test_flush_pending_emits_result_without_category(self) -> None:
        html = """
        <p class="TITLE">
          <a href="/test/Test-123.html">Orphan Result</a>
        </p>
        """
        parser = _SearchResultParser("https://byte.to")
        parser.feed(html)
        parser.flush_pending()

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Orphan Result"
        assert parser.results[0]["category"] == ""

    def test_relative_urls_resolved(self) -> None:
        html = """
        <p class="TITLE">
          <a href="/Filme/Test-1.html">Relative Link</a>
        </p>
        <a href="/?cat=1">Filme</a>
        """
        parser = _SearchResultParser("https://byte.to")
        parser.feed(html)
        parser.flush_pending()

        assert parser.results[0]["url"] == ("https://byte.to/Filme/Test-1.html")


# ---------------------------------------------------------------------------
# DetailPageParser tests
# ---------------------------------------------------------------------------


class TestDetailPageParser:
    def test_release_name_extracted(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.release_name == (
            "Batman.Forever.1995.GERMAN.DL.HDR.2160P.WEB.H265-SunDry"
        )

    def test_size_extracted(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.size == "7.14 GB"

    def test_category_extracted(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.category == "UHD - 2160p"

    def test_no_release_name_in_short_text(self) -> None:
        html = "<table><tr><td>Short</td></tr></table>"
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.release_name == ""

    def test_url_not_detected_as_release(self) -> None:
        html = (
            "<table><tr>"
            "<td>https://example.com/some.very.long.path.with.dots</td>"
            "</tr></table>"
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.release_name == ""

    def test_text_with_spaces_not_detected_as_release(self) -> None:
        html = (
            "<table><tr>"
            "<td>This is a long sentence with spaces not a release</td>"
            "</tr></table>"
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.release_name == ""


# ---------------------------------------------------------------------------
# IframeLinkParser tests
# ---------------------------------------------------------------------------


class TestIframeLinkParser:
    def test_links_with_img_alt_extracted(self) -> None:
        parser = _IframeLinkParser()
        parser.feed(_IFRAME_HTML)

        assert len(parser.links) == 2
        assert parser.links[0]["hoster"] == "rapidgator"
        assert parser.links[0]["link"] == ("https://hide.cx/container/uuid-111")
        assert parser.links[1]["hoster"] == "ddownload"
        assert parser.links[1]["link"] == ("https://byte.to/go.php?hash=abc")

    def test_links_without_img_use_text(self) -> None:
        html = """
        <a href="https://hide.cx/container/xyz">Online nitroflare.com</a>
        """
        parser = _IframeLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "nitroflare"

    def test_duplicate_urls_deduplicated(self) -> None:
        html = """
        <img alt="rapidgator.net">
        <a href="https://hide.cx/container/same">Online rapidgator.net</a>
        <img alt="rapidgator.net">
        <a href="https://hide.cx/container/same">Online rapidgator.net</a>
        """
        parser = _IframeLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1

    def test_links_without_hoster_skipped(self) -> None:
        html = """
        <a href="https://example.com/something">Just a link</a>
        """
        parser = _IframeLinkParser()
        parser.feed(html)

        assert len(parser.links) == 0

    def test_non_http_links_ignored(self) -> None:
        html = """
        <img alt="rapidgator.net">
        <a href="javascript:void(0)">Online rapidgator.net</a>
        """
        parser = _IframeLinkParser()
        parser.feed(html)

        assert len(parser.links) == 0

    def test_img_alt_without_dot_ignored(self) -> None:
        html = """
        <img alt="decoration">
        <a href="https://hide.cx/container/abc">Online rapidgator.net</a>
        """
        parser = _IframeLinkParser()
        parser.feed(html)

        # Should still extract via text pattern
        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "rapidgator"


# ---------------------------------------------------------------------------
# Category mapping tests
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    def test_torznab_to_site_movies(self) -> None:
        assert _TORZNAB_TO_SITE_CATEGORY[2000] == "1"

    def test_torznab_to_site_tv(self) -> None:
        assert _TORZNAB_TO_SITE_CATEGORY[5000] == "2"

    def test_torznab_to_site_games(self) -> None:
        assert _TORZNAB_TO_SITE_CATEGORY[4000] == "15"

    def test_torznab_to_site_music(self) -> None:
        assert _TORZNAB_TO_SITE_CATEGORY[3000] == "99"

    def test_torznab_to_site_books(self) -> None:
        assert _TORZNAB_TO_SITE_CATEGORY[7000] == "41"

    def test_unknown_torznab_fallback(self) -> None:
        assert _TORZNAB_TO_SITE_CATEGORY.get(9999, "") == ""

    def test_site_to_torznab_uhd(self) -> None:
        assert _site_category_to_torznab("UHD - 2160p") == 2000

    def test_site_to_torznab_serien(self) -> None:
        assert _site_category_to_torznab("Serien") == 5000

    def test_site_to_torznab_ebooks(self) -> None:
        assert _site_category_to_torznab("Ebooks") == 7000

    def test_site_to_torznab_hoerbuecher(self) -> None:
        assert _site_category_to_torznab("Hörbücher") == 7020

    def test_site_to_torznab_unknown_defaults_movies(self) -> None:
        assert _site_category_to_torznab("unknown") == 2000

    def test_site_to_torznab_case_insensitive(self) -> None:
        assert _site_category_to_torznab("SERIEN") == 5000
        assert _site_category_to_torznab("serien") == 5000


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        assert _make_plugin().name == "byte"

    def test_version(self) -> None:
        assert _make_plugin().version == "1.0.0"

    def test_mode(self) -> None:
        assert _make_plugin().mode == "playwright"


# ---------------------------------------------------------------------------
# Plugin search integration (mocked)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page(_SEARCH_HTML)
        # Pagination: page 2 returns empty → stops fetching more pages
        empty_page = _make_mock_page("<html><body></body></html>")
        detail_page_1 = _make_mock_detail_page(_DETAIL_HTML, _IFRAME_HTML)
        detail_page_2 = _make_mock_detail_page(_DETAIL_HTML, _IFRAME_HTML)

        context = _make_mock_context(
            pages=[search_page, empty_page, detail_page_1, detail_page_2]
        )

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("batman")

        assert len(results) == 2
        assert "Batman.Forever" in results[0].title
        assert "hide.cx" in results[0].download_link
        assert len(results[0].download_links) == 2
        assert results[0].download_links[0]["hoster"] == "rapidgator"
        assert results[0].download_links[1]["hoster"] == "ddownload"
        assert results[0].size == "7.14 GB"

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body></body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin.search("test", category=5000)

        # Verify the URL includes category parameter
        call_args = search_page.goto.call_args
        url_called = call_args[0][0]
        assert "c=2" in url_called  # TV = site category "2"
        assert "q=test" in url_called

    async def test_search_detail_without_links_skipped(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page(_SEARCH_SINGLE_HTML)
        # Detail page with no iframes
        detail_page = _make_mock_page(_DETAIL_HTML)

        context = _make_mock_context(pages=[search_page, detail_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")
        assert results == []

    async def test_search_detail_error_skipped(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page(_SEARCH_SINGLE_HTML)
        error_page = _make_mock_page()
        error_page.goto = AsyncMock(side_effect=Exception("timeout"))

        context = _make_mock_context(pages=[search_page, error_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")
        assert results == []


# ---------------------------------------------------------------------------
# Cloudflare wait tests
# ---------------------------------------------------------------------------


class TestCloudflareWait:
    async def test_no_challenge_passes(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        await plugin._wait_for_cloudflare(page)
        page.wait_for_function.assert_awaited_once()

    async def test_timeout_does_not_raise(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("timeout"))
        await plugin._wait_for_cloudflare(page)


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    async def test_cleanup_closes_resources(self) -> None:
        plugin = _make_plugin()

        context = _make_mock_context()
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        plugin._pw = pw
        plugin._browser = browser
        plugin._context = context

        await plugin.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert plugin._context is None
        assert plugin._browser is None
        assert plugin._pw is None

    async def test_cleanup_when_nothing_to_close(self) -> None:
        plugin = _make_plugin()
        await plugin.cleanup()
        # Should not raise
