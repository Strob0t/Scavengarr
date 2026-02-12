"""Tests for the ddlvalley.me Python plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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
_CATEGORY_PATH_MAP = _mod._CATEGORY_PATH_MAP
_is_hoster_domain = _mod._is_hoster_domain
_hoster_from_domain = _mod._hoster_from_domain


def _make_plugin() -> object:
    return _DDLValleyPlugin()


def _make_mock_page(content: str = "<html></html>") -> AsyncMock:
    page = AsyncMock()
    page.goto = AsyncMock()
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
    def test_movies_maps_correctly(self) -> None:
        assert _CATEGORY_PATH_MAP[2000] == "category/movies"

    def test_tv_maps_correctly(self) -> None:
        assert _CATEGORY_PATH_MAP[5000] == "category/tv-shows"

    def test_games_maps_correctly(self) -> None:
        assert _CATEGORY_PATH_MAP[4000] == "category/games"

    def test_music_maps_correctly(self) -> None:
        assert _CATEGORY_PATH_MAP[3000] == "category/music"

    def test_books_maps_correctly(self) -> None:
        assert _CATEGORY_PATH_MAP[7000] == "category/reading"

    def test_unknown_category_fallback(self) -> None:
        assert _CATEGORY_PATH_MAP.get(9999, "") == ""


# ---------------------------------------------------------------------------
# Plugin integration tests (with mocks)
# ---------------------------------------------------------------------------


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
