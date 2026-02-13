"""Tests for StealthPool — Playwright Stealth browser pool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scavengarr.infrastructure.hoster_resolvers.stealth_pool import (
    StealthPool,
    _BLOCKED_RESOURCE_TYPES,
    _OFFLINE_MARKERS,
    _block_resources,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mock_playwright_stack() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Return (playwright, browser, context) mocks wired together."""
    context = AsyncMock()
    context.new_page = AsyncMock()
    context.route = AsyncMock()
    context.close = AsyncMock()

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    playwright = AsyncMock()
    playwright.chromium = MagicMock()
    playwright.chromium.launch = AsyncMock(return_value=browser)
    playwright.stop = AsyncMock()

    return playwright, browser, context


def _mock_page(
    *,
    html: str = "<html><body>Player</body></html>",
    title: str = "Watch Video",
) -> AsyncMock:
    """Create a mock Page with goto, content, title, close."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value=html)
    page.title = AsyncMock(return_value=title)
    page.wait_for_function = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    page.close = AsyncMock()
    return page


# ------------------------------------------------------------------
# _block_resources
# ------------------------------------------------------------------


class TestBlockResources:
    """Route handler blocks heavy resource types."""

    @pytest.mark.parametrize("rtype", sorted(_BLOCKED_RESOURCE_TYPES))
    async def test_blocks_heavy_resource(self, rtype: str) -> None:
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = rtype
        await _block_resources(route)
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()

    @pytest.mark.parametrize("rtype", ["document", "script", "xhr", "fetch"])
    async def test_allows_essential_resources(self, rtype: str) -> None:
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = rtype
        await _block_resources(route)
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()


# ------------------------------------------------------------------
# StealthPool lifecycle
# ------------------------------------------------------------------


class TestStealthPoolLifecycle:
    """Browser init, stealth application, cleanup."""

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_ensure_context_launches_browser(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)

        stealth_instance = MagicMock()
        stealth_instance.apply_stealth_async = AsyncMock()
        mock_stealth_cls.return_value = stealth_instance

        pool = StealthPool(headless=True, timeout_ms=10_000)
        ctx = await pool._ensure_context()

        assert ctx is context
        pw.chromium.launch.assert_awaited_once_with(headless=True)
        browser.new_context.assert_awaited_once()
        stealth_instance.apply_stealth_async.assert_awaited_once_with(context)
        context.route.assert_awaited_once()

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_ensure_context_reuses_existing(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        pool = StealthPool()
        ctx1 = await pool._ensure_context()
        ctx2 = await pool._ensure_context()

        assert ctx1 is ctx2
        # launch only called once
        pw.chromium.launch.assert_awaited_once()

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_cleanup_closes_all_resources(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        pool = StealthPool()
        await pool._ensure_context()
        await pool.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert pool._context is None
        assert pool._browser is None
        assert pool._playwright is None

    async def test_cleanup_noop_when_not_started(self) -> None:
        pool = StealthPool()
        await pool.cleanup()  # no error


# ------------------------------------------------------------------
# StealthPool.probe_url
# ------------------------------------------------------------------


class TestStealthPoolProbe:
    """probe_url navigation and classification."""

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_alive_page(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        page = _mock_page(html="<html><body>Video Player</body></html>")
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(timeout_ms=5_000)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is True
        page.goto.assert_awaited_once()
        page.close.assert_awaited_once()

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    @pytest.mark.parametrize("marker", _OFFLINE_MARKERS)
    async def test_dead_page_offline_marker(
        self,
        mock_stealth_cls: MagicMock,
        mock_ap: MagicMock,
        marker: str,
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        page = _mock_page(html=f"<html><body>{marker}</body></html>")
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool()
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is False

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_navigation_error_returns_false(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        page = _mock_page()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_CONNECTION_REFUSED"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool()
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is False
        page.close.assert_awaited_once()

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_page_closed_even_on_error(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        page = _mock_page()
        page.content = AsyncMock(side_effect=RuntimeError("closed"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool()
        result = await pool.probe_url("https://example.com/e/abc")

        assert result is False
        page.close.assert_awaited_once()

    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.async_playwright"
    )
    @patch(
        "scavengarr.infrastructure.hoster_resolvers.stealth_pool.Stealth"
    )
    async def test_cf_wait_timeout_still_checks_content(
        self, mock_stealth_cls: MagicMock, mock_ap: MagicMock
    ) -> None:
        """Even if CF wait times out, content is still checked."""
        pw, browser, context = _mock_playwright_stack()
        mock_ap.return_value.start = AsyncMock(return_value=pw)
        mock_stealth_cls.return_value.apply_stealth_async = AsyncMock()

        page = _mock_page(
            html="<html><body>Video Player active</body></html>",
            title="Just a moment...",
        )
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("CF wait"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool()
        result = await pool.probe_url("https://example.com/e/abc")

        # CF wait timed out but page content is alive
        assert result is True
