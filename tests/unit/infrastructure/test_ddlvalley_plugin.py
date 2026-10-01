"""Tests for the ddlvalley.me Python plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "ddlvalley.py"


def _load_module() -> ModuleType:
    """Load ddlvalley.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("ddlvalley_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_DDLValleyPlugin = _mod.DDLValleyPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_TitleParser = _mod._TitleParser
_SECTION_CATEGORIES = _mod._SECTION_CATEGORIES
_CATEGORY_SECTIONS = _mod._CATEGORY_SECTIONS
_is_hoster_domain = _mod._is_hoster_domain
_hoster_from_domain = _mod._hoster_from_domain


def _make_plugin() -> object:
    return _DDLValleyPlugin()


_PAGING_DETAIL_HTML = """
<html><head><title>Post | DDLValley</title></head><body>
<div class="cont cl"><strong>Rapidgator</strong>
<a href="https://rapidgator.net/file/abc">DL</a></div>
</body></html>
"""


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
    browser.is_connected = MagicMock(return_value=True)
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
# Parser tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    def test_post_links_extracted(self) -> None:
        html = """
        <h2><a href="/some-movie-2025/">Some.Movie.2025</a></h2>
        <h2><a href="/other-show-s01/">Other.Show.S01</a></h2>
        """
        parser = _SearchResultParser("https://www.ddlvalley.me")
        parser.feed(html)

        assert len(parser.posts) == 2
        assert parser.posts[0]["title"] == "Some.Movie.2025"
        assert parser.posts[0]["url"] == ("https://www.ddlvalley.me/some-movie-2025/")
        assert parser.posts[1]["title"] == "Other.Show.S01"

    def test_external_links_excluded(self) -> None:
        html = """
        <h2><a href="https://other-site.com/post/">External</a></h2>
        <h2><a href="/local-post/">Local</a></h2>
        """
        parser = _SearchResultParser("https://www.ddlvalley.me")
        parser.feed(html)

        assert len(parser.posts) == 1
        assert parser.posts[0]["title"] == "Local"

    def test_duplicate_urls_deduplicated(self) -> None:
        html = """
        <h2><a href="/same-post/">Title</a></h2>
        <h2><a href="/same-post/">Title Again</a></h2>
        """
        parser = _SearchResultParser("https://www.ddlvalley.me")
        parser.feed(html)

        assert len(parser.posts) == 1

    def test_empty_title_skipped(self) -> None:
        html = '<h2><a href="/post/"></a></h2>'
        parser = _SearchResultParser("https://www.ddlvalley.me")
        parser.feed(html)

        assert len(parser.posts) == 0

    def test_non_h2_links_ignored(self) -> None:
        html = """
        <h3><a href="/sidebar-link/">Not a post</a></h3>
        <p><a href="/another-link/">Also not a post</a></p>
        <h2><a href="/real-post/">Real Post</a></h2>
        """
        parser = _SearchResultParser("https://www.ddlvalley.me")
        parser.feed(html)

        assert len(parser.posts) == 1
        assert parser.posts[0]["title"] == "Real Post"


class TestDetailPageParser:
    def test_hoster_links_extracted(self) -> None:
        html = """
        <div class="cont cl">
          <strong>Rapidgator</strong><br>
          <a href="https://rapidgator.net/file/abc">link1</a><br>
          <a href="https://rapidgator.net/file/def">link2</a><br>
          <strong>Uploaded</strong><br>
          <a href="https://ul.to/xyz">link3</a><br>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 3
        assert parser.links[0]["hoster"] == "rapidgator"
        assert parser.links[0]["link"] == ("https://rapidgator.net/file/abc")
        assert parser.links[2]["hoster"] == "uploaded"

    def test_non_hoster_links_excluded(self) -> None:
        html = """
        <div class="cont cl">
          <a href="https://www.imdb.com/title/tt123">IMDB</a>
          <a href="https://www.amazon.com/dp/B00">Amazon</a>
          <a href="https://rapidgator.net/file/abc">Download</a>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "rapidgator" in parser.links[0]["link"]

    def test_links_outside_cont_ignored(self) -> None:
        html = """
        <a href="https://rapidgator.net/file/outside">Outside</a>
        <div class="cont cl">
          <a href="https://rapidgator.net/file/inside">Inside</a>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "inside" in parser.links[0]["link"]

    def test_duplicate_links_deduplicated(self) -> None:
        html = """
        <div class="cont cl">
          <a href="https://rapidgator.net/file/abc">Link</a>
          <a href="https://rapidgator.net/file/abc">Dupe</a>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 1

    def test_nested_divs_handled(self) -> None:
        html = """
        <div class="cont cl">
          <div align="center">
            <div class="poster">
              <img src="poster.jpg">
            </div>
          </div>
          <strong>Go4up</strong>
          <a href="https://go4up.com/dl/abc">Download</a>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "go4up"

    def test_multiple_hosters_tracked(self) -> None:
        html = """
        <div class="cont cl">
          <strong>NitroFlare</strong>
          <a href="https://nitroflare.com/view/abc">NF</a>
          <strong>1Fichier</strong>
          <a href="https://1fichier.com/?xyz">1F</a>
          <strong>DDownload</strong>
          <a href="https://ddownload.com/abc">DD</a>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 3
        assert parser.links[0]["hoster"] == "nitroflare"
        assert parser.links[1]["hoster"] == "1fichier"
        assert parser.links[2]["hoster"] == "ddownload"

    def test_hoster_fallback_to_domain(self) -> None:
        """When no <strong> precedes a link, derive hoster from domain."""
        html = """
        <div class="cont cl">
          <a href="https://rapidgator.net/file/abc">Download</a>
        </div>
        """
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "rapidgator"


class TestTitleParser:
    def test_title_stripped(self) -> None:
        parser = _TitleParser()
        parser.feed("<title>Some.Movie.2025.1080p | DDLValley</title>")
        assert parser.title == "Some.Movie.2025.1080p"

    def test_title_without_suffix(self) -> None:
        parser = _TitleParser()
        parser.feed("<title>Plain Title</title>")
        assert parser.title == "Plain Title"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHosterHelpers:
    def test_known_domains_detected(self) -> None:
        assert _is_hoster_domain("rapidgator.net") is True
        assert _is_hoster_domain("rg.to") is True
        assert _is_hoster_domain("ul.to") is True
        assert _is_hoster_domain("go4up.com") is True
        assert _is_hoster_domain("nitroflare.com") is True
        assert _is_hoster_domain("ddownload.com") is True
        assert _is_hoster_domain("1fichier.com") is True

    def test_unknown_domains_rejected(self) -> None:
        assert _is_hoster_domain("imdb.com") is False
        assert _is_hoster_domain("ddlvalley.me") is False
        assert _is_hoster_domain("google.com") is False

    def test_hoster_from_domain(self) -> None:
        assert _hoster_from_domain("rapidgator.net") == "rapidgator"
        assert _hoster_from_domain("1fichier.com") == "1fichier"
        assert _hoster_from_domain("go4up.com") == "go4up"


class TestCategoryMapping:
    def test_sections(self) -> None:
        # Applications used to be 5020 (TV/Foreign), games 4000
        assert _SECTION_CATEGORIES == {
            "category/movies": 2000,
            "category/tv-shows": 5000,
            "category/games": 4050,
            "category/apps": 4000,
            "category/music": 3000,
            "category/reading": 7000,
        }

    def test_pc_request_searches_apps_and_games(self) -> None:
        assert _CATEGORY_SECTIONS[4000] == ("category/apps", "category/games")
        assert _CATEGORY_SECTIONS[4050] == ("category/games",)


# ---------------------------------------------------------------------------
# Plugin integration tests (with mocks)
# ---------------------------------------------------------------------------


def _detail(title: str) -> str:
    return (
        f"<html><head><title>{title} | DDLValley</title></head><body>"
        '<div class="cont cl"><strong>Rapidgator</strong>'
        '<a href="https://rapidgator.net/file/abc">DL</a></div></body></html>'
    )


class TestPluginAttributes:
    def test_name(self) -> None:
        p = _make_plugin()
        assert p.name == "ddlvalley"

    def test_version(self) -> None:
        p = _make_plugin()
        assert p.version == "1.0.0"

    def test_mode(self) -> None:
        p = _make_plugin()
        assert p.mode == "playwright"

    def test_language_is_english(self) -> None:
        # The registry reads ``languages``; a shadowing
        # ``default_language = "en"`` left the site registered as German
        p = _make_plugin()
        assert p.languages == ["en"]
        assert p.default_language == "en"


class TestPluginSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        search_html = """
        <html><body>
        <h2><a href="/movie-2025-1080p/">Movie.2025.1080p</a></h2>
        <h2><a href="/show-s01e01/">Show.S01E01</a></h2>
        </body></html>
        """

        detail_html = """
        <html>
        <head><title>Movie.2025.1080p | DDLValley</title></head>
        <body>
        <div class="cont cl">
          <strong>Rapidgator</strong>
          <a href="https://rapidgator.net/file/abc">DL</a>
          <strong>NitroFlare</strong>
          <a href="https://nitroflare.com/view/def">DL</a>
        </div>
        </body></html>
        """

        search_page = _make_mock_page(search_html)
        empty_page = _make_mock_page("<html></html>")
        detail_page_1 = _make_mock_page(detail_html)
        detail_page_2 = _make_mock_page(detail_html)

        context = _make_mock_context(
            pages=[search_page, empty_page, detail_page_1, detail_page_2]
        )

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("movie")

        assert len(results) == 2
        assert results[0].title == "Movie.2025.1080p"
        assert "rapidgator" in results[0].download_link
        assert len(results[0].download_links) == 2
        assert results[0].download_links[0]["hoster"] == "rapidgator"
        assert results[0].download_links[1]["hoster"] == "nitroflare"

    async def test_search_page_failure_keeps_collected_posts(self) -> None:
        """A timeout on page 2 must not discard page 1."""
        from patchright.async_api import TimeoutError as PlaywrightTimeoutError

        plugin = _make_plugin()
        search_page = _make_mock_page(
            '<html><body><h2><a href="/movie-2025/">Movie.2025</a></h2></body></html>'
        )
        failing_page = _make_mock_page()
        failing_page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Timeout"))
        detail_page = _make_mock_page(_PAGING_DETAIL_HTML)
        context = _make_mock_context(pages=[search_page, failing_page, detail_page])
        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("movie")

        assert [r.download_link for r in results] == ["https://rapidgator.net/file/abc"]

    async def test_search_stops_at_max_pages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A site that never runs out of pages must not be paged forever."""
        monkeypatch.setattr(_mod, "_MAX_PAGES", 2)
        plugin = _make_plugin()
        plugin._max_results = 5

        def _new_page() -> AsyncMock:
            page = _make_mock_page()
            state = {"url": ""}

            async def _goto(url: str, **_: object) -> AsyncMock:
                state["url"] = url
                return AsyncMock(status=200)

            async def _content() -> str:
                url = state["url"]
                if "?s=" not in url:
                    return _PAGING_DETAIL_HTML
                n = url.split("/page/")[1].split("/")[0] if "/page/" in url else "1"
                return f'<h2><a href="/post-{n}/">Post.{n}</a></h2>'

            page.goto = AsyncMock(side_effect=_goto)
            page.content = AsyncMock(side_effect=_content)
            return page

        context = _make_mock_context()
        context.new_page = AsyncMock(side_effect=lambda: _new_page())
        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("post")

        assert len(results) == 2

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_search_detail_without_links_skipped(self) -> None:
        plugin = _make_plugin()

        search_html = """
        <h2><a href="/no-links-post/">No Links</a></h2>
        """

        detail_html = """
        <html>
        <head><title>No Links | DDLValley</title></head>
        <body><div class="cont cl">Just text.</div></body>
        </html>
        """

        search_page = _make_mock_page(search_html)
        empty_page = _make_mock_page("<html></html>")
        detail_page = _make_mock_page(detail_html)

        context = _make_mock_context(pages=[search_page, empty_page, detail_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")
        assert results == []

    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body></body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin.search("test", category=5000)

        # Verify the URL includes category path
        call_args = search_page.goto.call_args
        url_called = call_args[0][0]
        assert "category/tv-shows" in url_called
        assert "s=test" in url_called

    async def test_results_carry_their_section(self) -> None:
        """Every result used to be labelled 2000."""
        plugin = _make_plugin()
        search_page = _make_mock_page(
            '<html><body><h2><a href="/show-s01/">Show.S01</a></h2></body></html>'
        )
        context = _make_mock_context(
            pages=[
                search_page,
                _make_mock_page("<html></html>"),
                _make_mock_page(_detail("Show.S01.German.1080p")),
            ]
        )
        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("show", category=5000)

        assert [r.category for r in results] == [5000]

    async def test_site_wide_results_by_title(self) -> None:
        plugin = _make_plugin()
        search_page = _make_mock_page(
            "<html><body>"
            '<h2><a href="/movie-2025/">Movie.2025.1080p</a></h2>'
            '<h2><a href="/show-s01e01/">Show.S01E01</a></h2>'
            "</body></html>"
        )
        context = _make_mock_context(
            pages=[
                search_page,
                _make_mock_page("<html></html>"),
                _make_mock_page(_detail("Movie.2025.1080p")),
                _make_mock_page(_detail("Show.S01E01.1080p")),
            ]
        )
        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")

        assert sorted((r.title, r.category) for r in results) == [
            ("Movie.2025.1080p", 8000),
            ("Show.S01E01.1080p", 5000),
        ]

    async def test_category_the_site_does_not_serve(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_browser = AsyncMock()

        assert await plugin.search("test", category=1000) == []
        plugin._ensure_browser.assert_not_awaited()

    async def test_search_query_is_url_encoded(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body></body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin.search("Fast & Furious")

        url_called = search_page.goto.call_args[0][0]
        assert url_called.endswith("/?s=Fast+%26+Furious")

    async def test_search_detail_error_skipped(self) -> None:
        plugin = _make_plugin()

        search_html = '<h2><a href="/post/">Title</a></h2>'

        search_page = _make_mock_page(search_html)
        empty_page = _make_mock_page("<html></html>")
        error_page = _make_mock_page()
        error_page.goto = AsyncMock(side_effect=Exception("timeout"))

        context = _make_mock_context(pages=[search_page, empty_page, error_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")
        assert results == []


class TestScrapeDetailRetry:
    """Post pages are rate-limited (nginx 503): fetched with retry backoff."""

    async def test_passes_retry_backoff(self) -> None:
        plugin = _make_plugin()
        plugin._fetch_page_html = AsyncMock(return_value="")

        result = await plugin._scrape_detail({"url": "https://www.ddlvalley.me/p/"})

        assert result is None
        plugin._fetch_page_html.assert_awaited_once_with(
            "https://www.ddlvalley.me/p/",
            wait_for_idle=False,
            retry_backoff_s=(2.0, 4.0),
        )


class TestCloudflareWait:
    async def test_no_challenge_passes(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        assert await plugin._wait_for_cloudflare(page) is True

    async def test_timeout_does_not_raise(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("timeout"))
        await plugin._wait_for_cloudflare(page)


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
