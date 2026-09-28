"""Tests for StealthPool — Patchright browser pool."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scavengarr.infrastructure.browser.stealth_pool import (
    _BLOCKED_RESOURCE_TYPES,
    _OFFLINE_MARKERS,
    StealthPool,
    _block_resources,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mock_pool_stack() -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Return (shared browser pool, browser, context) mocks wired together."""
    context = AsyncMock()
    context.new_page = AsyncMock()
    context.route = AsyncMock()
    context.close = AsyncMock()

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.is_connected = MagicMock(return_value=True)

    shared_pool = MagicMock()
    shared_pool.warmup = AsyncMock(return_value=(browser, MagicMock()))

    return shared_pool, browser, context


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
    """Context on the shared browser, resource blocking, cleanup."""

    async def test_ensure_context_uses_shared_browser(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        pool = StealthPool(browser_pool=shared_pool, timeout_ms=10_000)
        ctx = await pool._ensure_context()

        assert ctx is context
        shared_pool.warmup.assert_awaited_once()
        browser.new_context.assert_awaited_once()
        context.route.assert_awaited_once()

    async def test_ensure_context_reuses_existing(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        pool = StealthPool(browser_pool=shared_pool)
        ctx1 = await pool._ensure_context()
        ctx2 = await pool._ensure_context()

        assert ctx1 is ctx2
        shared_pool.warmup.assert_awaited_once()

    async def test_recreates_context_after_browser_relaunch(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)
        await pool._ensure_context()

        browser.is_connected = MagicMock(return_value=False)
        new_context = AsyncMock()
        new_browser = AsyncMock()
        new_browser.new_context = AsyncMock(return_value=new_context)
        new_browser.is_connected = MagicMock(return_value=True)
        shared_pool.warmup = AsyncMock(return_value=(new_browser, MagicMock()))

        assert await pool._ensure_context() is new_context

    async def test_cleanup_closes_only_context(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        pool = StealthPool(browser_pool=shared_pool)
        await pool._ensure_context()
        await pool.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_not_awaited()
        assert pool._context is None

    async def test_cleanup_noop_when_not_started(self) -> None:
        shared_pool, _, _ = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)
        await pool.cleanup()  # no error


# ------------------------------------------------------------------
# StealthPool.probe_url
# ------------------------------------------------------------------


class TestStealthPoolProbe:
    """probe_url navigation and classification."""

    async def test_alive_page(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page(html="<html><body>Video Player</body></html>")
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool, timeout_ms=5_000)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is True
        page.goto.assert_awaited_once()
        page.close.assert_awaited_once()

    @pytest.mark.parametrize("marker", _OFFLINE_MARKERS)
    async def test_dead_page_offline_marker(
        self,
        marker: str,
    ) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page(html=f"<html><body>{marker}</body></html>")
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is False

    async def test_navigation_error_returns_false(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_CONNECTION_REFUSED"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is False
        page.close.assert_awaited_once()

    async def test_page_closed_even_on_error(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page()
        page.content = AsyncMock(side_effect=RuntimeError("closed"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc")

        assert result is False
        page.close.assert_awaited_once()

    async def test_cf_wait_timeout_still_checks_content(self) -> None:
        """Even if CF wait times out, content is still checked."""
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page(
            html="<html><body>Video Player active</body></html>",
            title="Just a moment...",
        )
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("CF wait"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc")

        # CF wait timed out but page content is alive
        assert result is True


# ------------------------------------------------------------------
# StealthPool.fetch_text (BrowserFetcherPort)
# ------------------------------------------------------------------


def _fetch_page(
    *,
    status: int = 200,
    content_type: str = "text/html",
    html: str = "<html><body>real page</body></html>",
    title: str = "Real Page",
    fetched: str | None = '{"result": []}',
) -> AsyncMock:
    page = _mock_page(html=html, title=title)
    page.goto = AsyncMock(return_value=MagicMock(status=status))

    async def _evaluate(script: str, *args: object) -> object:
        return content_type if "contentType" in script else fetched

    page.evaluate = AsyncMock(side_effect=_evaluate)
    return page


class TestStealthPoolFetchText:
    async def test_is_a_browser_fetcher(self) -> None:
        from scavengarr.domain.ports import BrowserFetcherPort

        shared_pool, _, _ = _mock_pool_stack()
        assert isinstance(StealthPool(browser_pool=shared_pool), BrowserFetcherPort)

    async def test_returns_html_for_html_pages(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page()
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        assert text == "<html><body>real page</body></html>"
        page.close.assert_awaited_once()

    async def test_returns_raw_body_for_non_html(self) -> None:
        """JSON is re-fetched in-page: raw text, not Chrome's JSON viewer."""
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(content_type="application/json")
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/api/v2/search?q=x", timeout=10
        )

        assert text == '{"result": []}'

    async def test_solves_challenge_before_reading(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(status=403, title="Just a moment...")
        context.new_page = AsyncMock(return_value=page)
        pool = StealthPool(browser_pool=shared_pool)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=True),
        ) as mock_solve:
            text = await pool.fetch_text("https://filmfans.org/x", timeout=10)

        assert text == "<html><body>real page</body></html>"
        mock_solve.assert_awaited_once_with(page, timeout_ms=10_000)

    async def test_unsolved_challenge_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(
            return_value=_fetch_page(status=403, title="Just a moment...")
        )
        pool = StealthPool(browser_pool=shared_pool)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=False),
        ):
            assert await pool.fetch_text("https://filmfans.org/x", timeout=10) is None

    async def test_http_error_without_challenge_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(
            return_value=_fetch_page(status=404, title="Not Found")
        )

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/missing", timeout=10
        )

        assert text is None

    async def test_navigation_error_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page()
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_FAILED"))
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        assert text is None
        page.close.assert_awaited_once()

    async def test_concurrency_is_bounded(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        active = {"now": 0, "max": 0}

        async def _goto(*_: object, **__: object) -> MagicMock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.01)
            active["now"] -= 1
            return MagicMock(status=200)

        def _new_page() -> AsyncMock:
            page = _fetch_page()
            page.goto = AsyncMock(side_effect=_goto)
            return page

        context.new_page = AsyncMock(side_effect=lambda: _new_page())
        pool = StealthPool(browser_pool=shared_pool, fetch_concurrency=2)

        await asyncio.gather(
            *(pool.fetch_text(f"https://x.org/{i}", timeout=10) for i in range(6))
        )

        assert active["max"] == 2
