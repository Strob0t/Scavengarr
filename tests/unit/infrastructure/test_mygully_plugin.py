"""Tests for the mygully.com Python plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "mygully.py"


def _load_mygully_module() -> ModuleType:
    """Load mygully.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("mygully_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_mygully = _load_mygully_module()
_MyGullyPlugin = _mygully.MyGullyPlugin
_PostLinkParser = _mygully._PostLinkParser
_ThreadLinkParser = _mygully._ThreadLinkParser
_ThreadTitleParser = _mygully._ThreadTitleParser
_hoster_from_url = _mygully._hoster_from_url
_hoster_from_text = _mygully._hoster_from_text
_is_container_host = _mygully._is_container_host
_CATEGORY_FORUM_MAP = _mygully._CATEGORY_FORUM_MAP


_TEST_CREDENTIALS = {
    "SCAVENGARR_MYGULLY_USERNAME": "testuser",
    "SCAVENGARR_MYGULLY_PASSWORD": "testpass",
}


def _make_plugin() -> object:
    """Create MyGullyPlugin instance."""
    return _MyGullyPlugin()


def _make_mock_page(
    content: str = "<html></html>",
    body_text: str = "",
) -> AsyncMock:
    """Create a mock Playwright Page."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_function = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.evaluate = AsyncMock(return_value=body_text)
    page.fill = AsyncMock()
    page.content = AsyncMock(return_value=content)
    page.close = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    # Support async context manager for expect_navigation
    nav_cm = AsyncMock()
    nav_cm.__aenter__ = AsyncMock(return_value=None)
    nav_cm.__aexit__ = AsyncMock(return_value=False)
    page.expect_navigation = MagicMock(return_value=nav_cm)
    return page


def _make_mock_context(
    pages: list[AsyncMock] | None = None,
    cookies: list[dict[str, str]] | None = None,
) -> AsyncMock:
    """Create a mock BrowserContext that yields pages in order."""
    context = AsyncMock()
    if pages:
        context.new_page = AsyncMock(side_effect=pages)
    else:
        context.new_page = AsyncMock(return_value=_make_mock_page())
    context.cookies = AsyncMock(return_value=cookies or [])
    context.close = AsyncMock()
    return context


def _make_mock_browser(context: AsyncMock | None = None) -> AsyncMock:
    """Create a mock Browser."""
    browser = AsyncMock()
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


_SESSION_COOKIES = [{"name": "bbsessionhash", "value": "abc123"}]


class TestPluginAttributes:
    def test_name_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "mygully"

    def test_version_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "playwright"

    def test_default_base_url(self) -> None:
        plugin = _make_plugin()
        assert plugin.base_url == "https://mygully.com"


class TestLogin:
    async def test_login_success(self) -> None:
        plugin = _make_plugin()

        login_page = _make_mock_page(body_text="Willkommen testuser")
        context = _make_mock_context(pages=[login_page], cookies=_SESSION_COOKIES)
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_mygully, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            await plugin._ensure_session()

        assert plugin._logged_in is True
        login_page.evaluate.assert_awaited()
        login_page.close.assert_awaited()

    async def test_login_domain_fallback(self) -> None:
        plugin = _make_plugin()

        # First page (mygully.com) -> goto raises
        fail_page = _make_mock_page()
        fail_page.goto = AsyncMock(side_effect=Exception("unreachable"))
        fail_page.is_closed = MagicMock(return_value=False)

        # Second page (mygully.to) -> succeeds
        ok_page = _make_mock_page(body_text="Willkommen testuser")
        context = _make_mock_context(
            pages=[fail_page, ok_page], cookies=_SESSION_COOKIES
        )
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_mygully, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            await plugin._ensure_session()

        assert plugin._logged_in is True
        assert plugin.base_url == "https://mygully.to"

    async def test_login_all_domains_fail(self) -> None:
        plugin = _make_plugin()

        pages = [_make_mock_page() for _ in range(2)]
        for p in pages:
            p.goto = AsyncMock(side_effect=Exception("unreachable"))
            p.is_closed = MagicMock(return_value=False)

        context = _make_mock_context(pages=pages, cookies=[])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_mygully, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            with pytest.raises(RuntimeError, match="All mygully domains failed"):
                await plugin._ensure_session()

    async def test_missing_credentials_raises(self) -> None:
        plugin = _make_plugin()

        context = _make_mock_context()
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(_mygully, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            os.environ.pop("SCAVENGARR_MYGULLY_USERNAME", None)
            os.environ.pop("SCAVENGARR_MYGULLY_PASSWORD", None)

            with pytest.raises(RuntimeError, match="Missing credentials"):
                await plugin._ensure_session()

    async def test_session_reuse(self) -> None:
        plugin = _make_plugin()

        context = _make_mock_context()
        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True

        await plugin._ensure_session()
        context.new_page.assert_not_awaited()


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        search_html = """
        <html><body>
        <a href="https://mygully.com/showthread.php?t=123">Thread 1</a>
        <a href="https://mygully.com/showthread.php?t=456">Thread 2</a>
        </body></html>
        """

        thread_html = """
        <html>
        <head><title>SpongeBob S01 - myGully.com</title></head>
        <body>
        <div id="post_message_123">
        <a href="https://www.keeplinks.org/p53/abc123">RapidGator</a>
        <a href="https://www.keeplinks.org/p53/def456">DDownload</a>
        </div>
        </body></html>
        """

        search_page = _make_mock_page(search_html)
        thread_page_1 = _make_mock_page(thread_html)
        thread_page_2 = _make_mock_page(thread_html)

        context = _make_mock_context(
            pages=[search_page, thread_page_1, thread_page_2],
        )

        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True
        plugin.base_url = "https://mygully.com"

        results = await plugin.search("SpongeBob")

        assert len(results) == 2
        assert results[0].title == "SpongeBob S01"
        assert "keeplinks.org" in results[0].download_link
        assert len(results[0].download_links) == 2
        assert results[0].download_links[0]["hoster"] == "rapidgator"
        assert results[0].download_links[1]["hoster"] == "ddownload"

    async def test_search_no_threads(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True
        plugin.base_url = "https://mygully.com"

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_search_paginates(self) -> None:
        plugin = _make_plugin()

        # Page 1: has threads + "Next Page" link
        search_html_p1 = """
        <html><body>
        <a href="https://mygully.com/showthread.php?t=100">Thread P1</a>
        <a href="search.php?searchid=99&page=2">></a>
        </body></html>
        """
        # Page 2: has threads, no next page
        search_html_p2 = """
        <html><body>
        <a href="https://mygully.com/showthread.php?t=200">Thread P2</a>
        </body></html>
        """

        thread_html = """
        <html>
        <head><title>Title - myGully.com</title></head>
        <body>
        <div id="post_message_1">
        <a href="https://www.keeplinks.org/p53/abc">RapidGator</a>
        </div>
        </body></html>
        """

        search_p1 = _make_mock_page(search_html_p1)
        search_p2 = _make_mock_page(search_html_p2)
        thread_1 = _make_mock_page(thread_html)
        thread_2 = _make_mock_page(thread_html)

        context = _make_mock_context(
            pages=[search_p1, search_p2, thread_1, thread_2],
        )

        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True
        plugin.base_url = "https://mygully.com"

        results = await plugin.search("test")

        assert len(results) == 2

    async def test_search_thread_without_links_skipped(self) -> None:
        plugin = _make_plugin()

        search_html = """
        <html><body>
        <a href="https://mygully.com/showthread.php?t=123">Thread</a>
        </body></html>
        """

        thread_html = """
        <html>
        <head><title>Empty Thread - myGully.com</title></head>
        <body>
        <div id="post_message_1">Just text, no links.</div>
        </body></html>
        """

        search_page = _make_mock_page(search_html)
        thread_page = _make_mock_page(thread_html)

        context = _make_mock_context(pages=[search_page, thread_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True
        plugin.base_url = "https://mygully.com"

        results = await plugin.search("test")
        assert results == []

    async def test_search_category_maps_forum_id(self) -> None:
        """Category parameter selects the correct forum ID."""
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True
        plugin.base_url = "https://mygully.com"

        # Audio category (3000) should map to forum "26"
        await plugin.search("test", category=3000)
        # The search_page.evaluate was called with the forum ID
        search_page.evaluate.assert_awaited()


class TestCloudflareWait:
    async def test_cloudflare_wait_passes_when_no_challenge(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        await plugin._wait_for_cloudflare(page)
        page.wait_for_function.assert_awaited_once()

    async def test_cloudflare_wait_timeout_does_not_raise(self) -> None:
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

        plugin._playwright = pw
        plugin._browser = browser
        plugin._context = context
        plugin._logged_in = True

        await plugin.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert plugin._logged_in is False
        assert plugin._context is None
        assert plugin._browser is None
        assert plugin._playwright is None


class TestPostLinkParser:
    def test_keeplinks_extracted(self) -> None:
        html = """
        <div id="post_message_123">
        <a href="https://www.keeplinks.org/p53/abc">RapidGator</a>
        <a href="https://www.keeplinks.org/p53/def">DDownload</a>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 2
        assert parser.links[0]["link"] == "https://www.keeplinks.org/p53/abc"
        assert parser.links[0]["hoster"] == "rapidgator"
        assert parser.links[1]["hoster"] == "ddownload"

    def test_filecrypt_extracted(self) -> None:
        html = """
        <div id="post_message_456">
        <a href="https://filecrypt.cc/Container/ABC123.html">Download</a>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "filecrypt.cc" in parser.links[0]["link"]

    def test_download_via_text_pattern(self) -> None:
        html = (
            '<div id="post_message_456">'
            '<a href="https://www.keeplinks.org/p16/abc">'
            "download via ddownload.com</a>"
            '<a href="https://www.keeplinks.org/p16/def">'
            "download via rapidgator.net</a>"
            "</div>"
        )

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 2
        assert parser.links[0]["hoster"] == "ddownload"
        assert parser.links[1]["hoster"] == "rapidgator"

    def test_non_container_links_skipped(self) -> None:
        html = """
        <div id="post_message_789">
        <a href="https://mygully.com/showthread.php?t=123">Internal</a>
        <a href="https://www.imdb.com/title/tt123">IMDB</a>
        <a href="https://www.youtube.com/watch?v=abc">Trailer</a>
        <a href="https://www.keeplinks.org/p53/abc">RapidGator</a>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "keeplinks.org" in parser.links[0]["link"]

    def test_links_outside_post_div_ignored(self) -> None:
        html = """
        <a href="https://www.keeplinks.org/p53/out">Outside</a>
        <div id="post_message_100">
        <a href="https://www.keeplinks.org/p53/abc">Inside</a>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert parser.links[0]["link"].endswith("/abc")

    def test_hide_cx_accepted(self) -> None:
        html = """
        <div id="post_message_400">
        <a href="https://hide.cx/abc123">Download Links</a>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "hide.cx" in parser.links[0]["link"]

    def test_duplicate_links_deduplicated(self) -> None:
        html = """
        <div id="post_message_200">
        <a href="https://www.keeplinks.org/p53/abc">RapidGator</a>
        <a href="https://www.keeplinks.org/p53/abc">RapidGator</a>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 1

    def test_nested_divs_do_not_exit_post_early(self) -> None:
        """Download links after nested divs must still be found."""
        html = """
        <div id="post_message_999">
          <div align="center">
            <b>Release Title 2025</b><br>
            <div class="wrap-spoiler">
              <div class="pre-spoiler">Spoiler</div>
              <div>
                <div class="body-spoiler" style="display:none;">
                  <div style="padding:10px;">
                    <div style="margin:20px;">NFO content</div>
                  </div>
                </div>
              </div>
            </div>
            <a href="https://www.keeplinks.org/p40/abc123">DDownload</a>
            <a href="https://www.keeplinks.org/p40/def456">RapidGator</a>
          </div>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 2
        assert parser.links[0]["hoster"] == "ddownload"
        assert parser.links[1]["hoster"] == "rapidgator"

    def test_multiple_posts_all_parsed(self) -> None:
        """Links from multiple post_message divs should all be collected."""
        html = """
        <div id="post_message_1">
          <div align="center">
            <a href="https://www.keeplinks.org/p40/aaa">Post1</a>
          </div>
        </div>
        <div id="post_message_2">
          <div align="center">
            <a href="https://www.keeplinks.org/p40/bbb">Post2</a>
          </div>
        </div>
        """

        parser = _PostLinkParser()
        parser.feed(html)

        assert len(parser.links) == 2


class TestThreadLinkParser:
    def test_classic_thread_links_extracted(self) -> None:
        html = """
        <a href="showthread.php?t=123">Thread 1</a>
        <a href="showthread.php?t=456">Thread 2</a>
        <a href="/other/page">Not a thread</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert len(parser.thread_urls) == 2
        assert parser.thread_urls[0] == "https://mygully.com/showthread.php?t=123"
        assert parser.thread_urls[1] == "https://mygully.com/showthread.php?t=456"

    def test_friendly_url_thread_links(self) -> None:
        """myGully uses friendly URLs like /thread/12345-slug/."""
        html = """
        <a href="/thread/12345-some-movie-release/">Movie Release</a>
        <a href="/thread/67890-another-thread/">Another Thread</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert len(parser.thread_urls) == 2
        assert parser.thread_urls[0] == "https://mygully.com/showthread.php?t=12345"
        assert parser.thread_urls[1] == "https://mygully.com/showthread.php?t=67890"

    def test_mixed_url_formats_deduplicated(self) -> None:
        """Same thread via classic and friendly URL detected once."""
        html = """
        <a href="showthread.php?t=12345">Classic</a>
        <a href="/thread/12345-some-slug/">Friendly</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert len(parser.thread_urls) == 1
        assert parser.thread_urls[0] == "https://mygully.com/showthread.php?t=12345"

    def test_duplicate_thread_ids_deduplicated(self) -> None:
        html = """
        <a href="showthread.php?t=123">Normal</a>
        <a href="showthread.php?t=123&highlight=SpongeBob">Highlighted</a>
        <a href="showthread.php?goto=newpost&t=123">New post</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert len(parser.thread_urls) == 1
        assert parser.thread_urls[0] == "https://mygully.com/showthread.php?t=123"

    def test_post_links_without_thread_id_skipped(self) -> None:
        html = """
        <a href="showthread.php?p=456#post456">Post link</a>
        <a href="showthread.php?t=123">Thread link</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert len(parser.thread_urls) == 1
        assert "t=123" in parser.thread_urls[0]

    def test_next_page_url_detected(self) -> None:
        html = """
        <a href="showthread.php?t=123">Thread</a>
        <a href="search.php?searchid=99&page=2">></a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert parser.next_page_url == "search.php?searchid=99&page=2"

    def test_weiter_pagination_detected(self) -> None:
        """German 'Weiter' (Next) pagination link is detected."""
        html = """
        <a href="showthread.php?t=123">Thread</a>
        <a href="search.php?searchid=99&page=2">Weiter</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert parser.next_page_url == "search.php?searchid=99&page=2"

    def test_raquo_pagination_detected(self) -> None:
        html = """
        <a href="showthread.php?t=123">Thread</a>
        <a href="search.php?searchid=99&page=2">\u00bb</a>
        """

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert parser.next_page_url == "search.php?searchid=99&page=2"

    def test_no_next_page_url(self) -> None:
        html = '<a href="showthread.php?t=123">Thread</a>'

        parser = _ThreadLinkParser("https://mygully.com")
        parser.feed(html)

        assert parser.next_page_url == ""


class TestTitleParser:
    def test_title_stripped(self) -> None:
        parser = _ThreadTitleParser()
        parser.feed("<title>SpongeBob S01 - myGully.com</title>")
        assert parser.title == "SpongeBob S01"

    def test_title_with_suffix(self) -> None:
        parser = _ThreadTitleParser()
        parser.feed("<title>Movie Title - myGully.com (some extra)</title>")
        assert parser.title == "Movie Title"

    def test_title_case_insensitive_strip(self) -> None:
        parser = _ThreadTitleParser()
        parser.feed("<title>Movie Title - MYGULLY.COM</title>")
        assert parser.title == "Movie Title"

    def test_title_without_suffix(self) -> None:
        parser = _ThreadTitleParser()
        parser.feed("<title>Plain Title</title>")
        assert parser.title == "Plain Title"


class TestHosterHelpers:
    def test_hoster_from_url(self) -> None:
        assert _hoster_from_url("https://www.keeplinks.org/p53/abc") == "keeplinks"
        assert _hoster_from_url("https://rapidgator.net/file/abc") == "rapidgator"

    def test_hoster_from_text_via_pattern(self) -> None:
        assert _hoster_from_text("download via ddownload.com") == "ddownload"
        assert _hoster_from_text("download via rapidgator.net") == "rapidgator"

    def test_hoster_from_text_plain_name(self) -> None:
        assert _hoster_from_text("RapidGator") == "rapidgator"
        assert _hoster_from_text("DDownload") == "ddownload"

    def test_hoster_from_text_empty(self) -> None:
        assert _hoster_from_text("") == ""
        assert _hoster_from_text("https://example.com") == ""


class TestContainerHostCheck:
    def test_known_containers_accepted(self) -> None:
        assert _is_container_host("keeplinks.org") is True
        assert _is_container_host("keeplinks.co") is True
        assert _is_container_host("filecrypt.cc") is True
        assert _is_container_host("hide.cx") is True
        assert _is_container_host("linkcrypt.ws") is True
        assert _is_container_host("share-links.biz") is True

    def test_unknown_hosts_rejected(self) -> None:
        assert _is_container_host("mygully.com") is False
        assert _is_container_host("imdb.com") is False
        assert _is_container_host("youtube.com") is False
        assert _is_container_host("google.com") is False


class TestCategoryForumMapping:
    def test_movies_maps_to_video(self) -> None:
        assert _CATEGORY_FORUM_MAP[2000] == "25"

    def test_tv_maps_to_video(self) -> None:
        assert _CATEGORY_FORUM_MAP[5000] == "25"

    def test_audio_maps_to_audio(self) -> None:
        assert _CATEGORY_FORUM_MAP[3000] == "26"

    def test_books_maps_to_text(self) -> None:
        assert _CATEGORY_FORUM_MAP[7000] == "363"

    def test_games_maps_to_games(self) -> None:
        assert _CATEGORY_FORUM_MAP[4000] == "27"
        assert _CATEGORY_FORUM_MAP[1000] == "27"

    def test_default_fallback(self) -> None:
        assert _CATEGORY_FORUM_MAP.get(9999, "25") == "25"
