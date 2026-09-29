"""Tests for the byte.to Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

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
_WidgetLinkParser = _mod._WidgetLinkParser
_SEARCH_CATEGORY = _mod._SEARCH_CATEGORY
_SITE_CATEGORY_MAP = _mod._SITE_CATEGORY_MAP
_site_category_to_torznab = _mod._site_category_to_torznab


def _make_plugin() -> object:
    return _BytePlugin()


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

# Real-world markup: label and value share a cell; one widget iframe per link
_WIDGET_1 = "https://byte.to/widgets/button.php?AAA111"
_WIDGET_2 = "https://byte.to/widgets/button.php?BBB222"
_WIDGET_3 = "https://byte.to/widgets/button.php?CCC333"

_DETAIL_HTML = f"""
<html><body>
<table>
  <tr><td>Batman.Forever.1995.GERMAN.DL.HDR.2160P.WEB.H265-SunDry</td></tr>
  <TR><TD ALIGN="LEFT" COLSPAN="2"><B>Kategorie:</B> UHD - 2160p</TD></TR>
  <TR><TD ALIGN="LEFT"><B>Release Jahr:</B> 1995</TD></TR>
  <TR><TD ALIGN="LEFT"><B>Gr&ouml;&szlig;e:</B> 7,14 GB</TD></TR>
</table>
<table>
  <tr><th>Mirror #1 von uploader | Passwort: keine Angabe</th></tr>
  <tr><td>
    <iframe frameBorder="0" src="{_WIDGET_1}" align="left">loading ...</iframe>
    <iframe frameBorder="0" src="{_WIDGET_2}" align="left">loading ...</iframe>
    <iframe frameBorder="0" src="{_WIDGET_3}" align="left">loading ...</iframe>
  </td></tr>
</table>
</body></html>
"""


def _widget_html(href: str, host: str, dot: str = "green-dot") -> str:
    return (
        "<html><head><style>.green-dot{}</style></head><body>"
        f'<a href="{href}" target="_blank" class="loadbutton">'
        f'<span class="{dot}" title="Online"></span>'
        f"<img src='/widgets/favicons/{host}.ico' title='{host}' /> {host}</a>"
        "</body></html>"
    )


_WIDGET_1_HTML = _widget_html("https://hide.cx/container/uuid-111", "rapidgator.net")
_WIDGET_2_HTML = _widget_html("https://byte.to/go.php?hash=abc", "ddownload.com")
_WIDGET_3_HTML = _widget_html(
    "https://hide.cx/container/uuid-333", "nitroflare.com", dot="red-dot"
)


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

        assert parser.size == "7,14 GB"

    def test_category_extracted(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.category == "UHD - 2160p"

    def test_widget_urls_extracted(self) -> None:
        parser = _DetailPageParser()
        parser.feed(_DETAIL_HTML)

        assert parser.widget_urls == [_WIDGET_1, _WIDGET_2, _WIDGET_3]

    def test_other_iframes_ignored(self) -> None:
        parser = _DetailPageParser()
        parser.feed('<iframe src="//byte.to/topcover/top.php"></iframe>')

        assert parser.widget_urls == []

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
# WidgetLinkParser tests
# ---------------------------------------------------------------------------


class TestWidgetLinkParser:
    def test_link_with_img_title_extracted(self) -> None:
        parser = _WidgetLinkParser()
        parser.feed(_WIDGET_1_HTML)

        assert parser.links == [
            {"hoster": "rapidgator", "link": "https://hide.cx/container/uuid-111"}
        ]

    def test_link_without_img_uses_text(self) -> None:
        parser = _WidgetLinkParser()
        parser.feed('<a href="https://hide.cx/container/xyz">Online nitroflare.com</a>')

        assert parser.links == [
            {"hoster": "nitroflare", "link": "https://hide.cx/container/xyz"}
        ]

    def test_offline_link_skipped(self) -> None:
        parser = _WidgetLinkParser()
        parser.feed(_WIDGET_3_HTML)

        assert parser.links == []

    def test_non_http_link_ignored(self) -> None:
        parser = _WidgetLinkParser()
        parser.feed('<a href="javascript:void(0)"><img title="rapidgator.net"> x</a>')

        assert parser.links == []


# ---------------------------------------------------------------------------
# Category mapping tests
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    """Categories of the live site menu (checked 2026-09-29)."""

    @pytest.mark.parametrize(
        ("name", "category"),
        [
            ("UHD - 2160p", 2000),
            ("HD - 1080p x265", 2000),
            ("HD - 1080p Englisch", 2010),
            ("Serien", 5000),
            ("Einzelne Folgen", 5000),
            ("Ganze Staffeln", 5000),
            ("Dokumentation", 5080),
            ("MicroHD Dokus", 5080),
            ("Win", 4050),
            ("Mac OS", 4050),
            ("Konsolen", 1000),
            ("PS4", 1180),
            ("Nintendo Wii", 1030),
            ("Windows", 4000),
            ("Mac", 4030),
            ("Android", 4070),
            ("Alben", 3000),
            ("Lossless", 3040),
            ("Konzerte & Videos", 3020),
            ("Hörbücher", 3030),
            ("Ebooks", 7000),
            ("Magazine", 7000),
            ("Clips", 6000),
        ],
    )
    def test_site_categories(self, name: str, category: int) -> None:
        assert _site_category_to_torznab(name) == category

    def test_unknown_is_other(self) -> None:
        # Used to be a film, as were episodes and Windows software
        assert _site_category_to_torznab("unknown") == 8000

    def test_case_insensitive(self) -> None:
        assert _site_category_to_torznab("SERIEN") == 5000
        assert _site_category_to_torznab("serien") == 5000

    def test_search_categories(self) -> None:
        # Only site groups holding a whole Torznab family are searched by ID
        assert _SEARCH_CATEGORY == {2000: "1", 5000: "2", 7000: "41", 6000: "46"}


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        assert _make_plugin().name == "byte"

    def test_mode(self) -> None:
        assert _make_plugin().mode == "httpx"


# ---------------------------------------------------------------------------
# Plugin search integration (respx)
# ---------------------------------------------------------------------------

_DETAIL_1 = "https://byte.to/Filme/UHD-2160p/Batman-Forever-12345.html"
_DETAIL_2 = "https://byte.to/Tv/Serien/Batman-S01-67890.html"


def _mock_site(search_html: str, detail_html: str = _DETAIL_HTML) -> respx.Route:
    """Route byte.to: search page, detail pages, widgets, go.php redirector."""
    respx.get(_WIDGET_1).respond(200, text=_WIDGET_1_HTML)
    respx.get(_WIDGET_2).respond(200, text=_WIDGET_2_HTML)
    respx.get(_WIDGET_3).respond(200, text=_WIDGET_3_HTML)
    respx.get("https://byte.to/go.php?hash=abc").respond(
        302, headers={"Location": "https://filecrypt.cc/Container/ABC.html"}
    )
    respx.get(_DETAIL_1).respond(200, text=detail_html)
    respx.get(_DETAIL_2).respond(200, text=detail_html)
    respx.get("https://byte.to/Filme/HD-1080p/Test-Movie-111.html").respond(
        200, text=detail_html
    )

    def _search(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("start", "1")
        return httpx.Response(200, text=search_html if page == "1" else "")

    return respx.get("https://byte.to/").mock(side_effect=_search)


class TestPluginSearch:
    @respx.mock
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        _mock_site(_SEARCH_HTML)

        results = await plugin.search("batman")
        await plugin.cleanup()

        assert len(results) == 2
        first = results[0]
        assert first.title == "Batman.Forever.1995.GERMAN.DL.HDR.2160P.WEB.H265-SunDry"
        assert first.size == "7,14 GB"
        assert first.category == 2000
        assert first.source_url == _DETAIL_1
        # go.php resolved to its target, offline nitroflare link dropped
        assert first.download_links == [
            {"hoster": "rapidgator", "link": "https://hide.cx/container/uuid-111"},
            {"hoster": "ddownload", "link": "https://filecrypt.cc/Container/ABC.html"},
        ]
        assert first.download_link == "https://hide.cx/container/uuid-111"

    @respx.mock
    async def test_search_paginates_until_empty_page(self) -> None:
        plugin = _make_plugin()
        search = _mock_site(_SEARCH_HTML)

        await plugin.search("batman")
        await plugin.cleanup()

        # max_page=3 in the fixture; page 2 is empty -> page 3 never fetched
        starts = [c.request.url.params.get("start") for c in search.calls]
        assert starts == [None, "2"]

    @respx.mock
    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        _mock_site("<html><body>No results</body></html>")

        results = await plugin.search("nonexistent")
        await plugin.cleanup()

        assert results == []

    @respx.mock
    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()
        search = _mock_site("<html><body></body></html>")

        await plugin.search("test", category=5000)
        await plugin.cleanup()

        params = search.calls[0].request.url.params
        assert params["q"] == "test"
        assert params["t"] == "1"
        assert params["c"] == "2"  # TV = site category "2"

    @respx.mock
    async def test_movie_request_keeps_the_films(self) -> None:
        plugin = _make_plugin()
        search = _mock_site(_SEARCH_HTML)

        results = await plugin.search("batman", category=2000)
        await plugin.cleanup()

        assert search.calls[0].request.url.params["c"] == "1"
        # the series row of the page is dropped before its page is loaded
        assert [r.category for r in results] == [2000]

    @respx.mock
    async def test_pc_request_searches_every_group(self) -> None:
        plugin = _make_plugin()
        search = _mock_site(_SEARCH_HTML)

        results = await plugin.search("batman", category=4000)
        await plugin.cleanup()

        # games (Spiele) and programs (Programme) are separate groups
        assert "c" not in search.calls[0].request.url.params
        assert results == []

    @respx.mock
    async def test_category_the_site_does_not_serve(self) -> None:
        plugin = _make_plugin()
        search = _mock_site(_SEARCH_HTML)

        assert await plugin.search("batman", category=8000) == []
        await plugin.cleanup()
        assert not search.called

    @respx.mock
    async def test_search_detail_without_widgets_skipped(self) -> None:
        plugin = _make_plugin()
        _mock_site(_SEARCH_SINGLE_HTML, detail_html="<html><body></body></html>")

        results = await plugin.search("test")
        await plugin.cleanup()

        assert results == []

    @respx.mock
    async def test_search_detail_error_skipped(self) -> None:
        plugin = _make_plugin()
        _mock_site(_SEARCH_SINGLE_HTML)
        # Same pattern as the detail route of _mock_site -> replaces it
        respx.get("https://byte.to/Filme/HD-1080p/Test-Movie-111.html").respond(500)

        results = await plugin.search("test")
        await plugin.cleanup()

        assert results == []
