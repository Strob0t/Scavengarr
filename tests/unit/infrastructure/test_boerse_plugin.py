"""Tests for the boerse.sx Python plugin (Playwright-based)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "boerse.py"


def _load_boerse_module() -> ModuleType:
    """Load boerse.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("boerse_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_boerse = _load_boerse_module()
_BoersePlugin = _boerse.BoersePlugin
_LinkParser = _boerse._LinkParser
_ThreadLinkParser = _boerse._ThreadLinkParser
_hoster_from_url = _boerse._hoster_from_url


_TEST_CREDENTIALS = {
    "SCAVENGARR_BOERSE_USERNAME": "testuser",
    "SCAVENGARR_BOERSE_PASSWORD": "testpass",
}


def _make_plugin() -> object:
    """Create BoersePlugin instance."""
    return _BoersePlugin()


def _make_mock_page(content: str = "<html></html>") -> AsyncMock:
    """Create a mock Playwright Page."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_function = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.evaluate = AsyncMock()
    page.content = AsyncMock(return_value=content)
    page.close = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
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


class TestLogin:
    async def test_login_success(self) -> None:
        plugin = _make_plugin()

        login_page = _make_mock_page()
        cookies = [{"name": "bb_userid", "value": "12345"}]
        context = _make_mock_context(pages=[login_page], cookies=cookies)
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_boerse, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            await plugin._ensure_session()

        assert plugin._logged_in is True
        login_page.evaluate.assert_awaited_once()
        login_page.close.assert_awaited()

    async def test_login_domain_fallback(self) -> None:
        plugin = _make_plugin()

        # First page (boerse.am) → goto raises, second page (boerse.sx) → succeeds
        fail_page = _make_mock_page()
        fail_page.goto = AsyncMock(side_effect=Exception("unreachable"))
        # is_closed returns False so cleanup tries page.close
        fail_page.is_closed = MagicMock(return_value=False)

        ok_page = _make_mock_page()
        cookies = [{"name": "bb_userid", "value": "12345"}]
        context = _make_mock_context(pages=[fail_page, ok_page], cookies=cookies)
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_boerse, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            await plugin._ensure_session()

        assert plugin._logged_in is True
        assert plugin.base_url == "https://boerse.sx"

    async def test_login_all_domains_fail(self) -> None:
        plugin = _make_plugin()

        # All pages raise on goto
        pages = [_make_mock_page() for _ in range(5)]
        for p in pages:
            p.goto = AsyncMock(side_effect=Exception("unreachable"))
            p.is_closed = MagicMock(return_value=False)

        context = _make_mock_context(pages=pages, cookies=[])
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_boerse, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            with pytest.raises(RuntimeError, match="All boerse domains failed"):
                await plugin._ensure_session()

    async def test_missing_credentials_raises(self) -> None:
        plugin = _make_plugin()

        # Set up browser mocks so _ensure_browser() succeeds
        context = _make_mock_context()
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        mock_start = AsyncMock(return_value=pw)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(_boerse, "async_playwright") as mock_ap,
        ):
            mock_ap.return_value.start = mock_start
            os.environ.pop("SCAVENGARR_BOERSE_USERNAME", None)
            os.environ.pop("SCAVENGARR_BOERSE_PASSWORD", None)

            with pytest.raises(RuntimeError, match="Missing credentials"):
                await plugin._ensure_session()

    async def test_session_reuse(self) -> None:
        plugin = _make_plugin()

        context = _make_mock_context()
        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True

        await plugin._ensure_session()
        # No new pages should be created — session was already active
        context.new_page.assert_not_awaited()


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        search_html = """
        <html><body>
        <a href="https://boerse.am/showthread.php?t=123">Thread 1</a>
        <a href="https://boerse.am/showthread.php?t=456">Thread 2</a>
        </body></html>
        """

        thread_html = """
        <html><head><title>SpongeBob S01 - boerse.am</title></head><body>
        <a href="https://boerse.am/abc123" target="_blank">https://veev.to/dl/spongebob</a>
        <a href="https://boerse.am/def456" target="_blank">https://dood.to/dl/spongebob</a>
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
        plugin.base_url = "https://boerse.am"

        results = await plugin.search("SpongeBob")

        assert len(results) == 2
        assert results[0].title == "SpongeBob S01"
        assert results[0].download_link == "https://veev.to/dl/spongebob"
        assert len(results[0].download_links) == 2

    async def test_search_no_threads(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[search_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context
        plugin._logged_in = True
        plugin.base_url = "https://boerse.am"

        results = await plugin.search("nonexistent")
        assert results == []


class TestCloudflareWait:
    async def test_cloudflare_wait_passes_when_no_challenge(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        # wait_for_function succeeds immediately (no Cloudflare)
        await plugin._wait_for_cloudflare(page)
        page.wait_for_function.assert_awaited_once()

    async def test_cloudflare_wait_timeout_does_not_raise(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("timeout"))

        # Should not raise — timeout is swallowed
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


class TestDownloadLinkExtraction:
    def test_anonymized_links_extracted(self) -> None:
        html = """
        <a href="https://boerse.am/abc123" target="_blank">https://veev.to/actual-file</a>
        <a href="https://boerse.am/def456" target="_blank">https://dood.to/another-file</a>
        <a href="/some/internal/link">Click here</a>
        """

        parser = _LinkParser()
        parser.feed(html)

        assert len(parser.links) == 2
        assert parser.links[0] == "https://veev.to/actual-file"
        assert parser.links[1] == "https://dood.to/another-file"

    def test_non_http_text_ignored(self) -> None:
        html = """
        <a href="https://boerse.am/abc">Click for download</a>
        <a href="https://boerse.am/def">ftp://something</a>
        """

        parser = _LinkParser()
        parser.feed(html)
        assert len(parser.links) == 0

    def test_thread_link_parser(self) -> None:
        html = """
        <a href="showthread.php?t=123">Thread 1</a>
        <a href="/threads/456-some-thread">Thread 2</a>
        <a href="/other/page">Not a thread</a>
        """

        parser = _ThreadLinkParser("https://boerse.am")
        parser.feed(html)

        assert len(parser.thread_urls) == 2
        assert parser.thread_urls[0] == "https://boerse.am/showthread.php?t=123"
        assert parser.thread_urls[1] == "https://boerse.am/threads/456-some-thread"

    def test_hoster_from_url(self) -> None:
        assert _hoster_from_url("https://veev.to/dl/file") == "veev"
        assert _hoster_from_url("https://www.dood.to/dl/file") == "dood"
        assert _hoster_from_url("https://voe.sx/embed/abc") == "voe"
