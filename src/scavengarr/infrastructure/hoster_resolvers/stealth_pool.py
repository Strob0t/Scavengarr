"""Playwright Stealth browser pool for Cloudflare bypass probing.

Manages a single Chromium instance with stealth evasions applied.
Pages are created per-probe and closed immediately after.
Resource blocking (images, fonts, CSS, media) keeps navigation fast.
"""

from __future__ import annotations

import asyncio

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright_stealth import Stealth

from scavengarr.infrastructure.hoster_resolvers.cloudflare import _CF_MARKERS

log = structlog.get_logger(__name__)

_BLOCKED_RESOURCE_TYPES = frozenset(
    {"image", "font", "stylesheet", "media", "texttrack"}
)

_OFFLINE_MARKERS: tuple[str, ...] = (
    "File Not Found",
    "file was removed",
    "no longer available",
    "has been removed",
    "File is no longer",
    "deleted by the owner",
    "This file is no longer",
    "Video not found or has been removed",
    "video_deleted",
    "class=\"removed\"",
    "class=\"deleted\"",
    'class="fake-signup"',
)


async def _block_resources(route: Route) -> None:
    """Abort requests for heavy resource types."""
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


class StealthPool:
    """Lazy-init Playwright Stealth pool for Cloudflare bypass probing.

    Usage::

        pool = StealthPool(headless=True, timeout_ms=15_000)
        alive = await pool.probe_url("https://example.com/embed/abc")
        await pool.cleanup()
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 15_000,
    ) -> None:
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_context(self) -> BrowserContext:
        """Launch browser + stealth context (double-check lock)."""
        if self._context is not None:
            return self._context
        async with self._lock:
            if self._context is not None:
                return self._context

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
            )
            self._context = await self._browser.new_context()

            # Apply stealth evasions to the context (all future pages inherit)
            stealth = Stealth()
            await stealth.apply_stealth_async(self._context)

            # Block heavy resources on all pages in this context
            await self._context.route("**/*", _block_resources)

            log.info("stealth_pool_started", headless=self._headless)
            return self._context

    async def cleanup(self) -> None:
        """Close context, browser, and Playwright — idempotent."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def new_page(self) -> Page:
        """Create a new page in the stealth context."""
        ctx = await self._ensure_context()
        return await ctx.new_page()

    async def probe_url(self, url: str, *, timeout: float = 10) -> bool:
        """Navigate to *url* in a stealth page, wait for CF to clear.

        Returns ``True`` when the page is alive (no offline markers),
        ``False`` when it is dead or navigation fails.
        """
        page: Page | None = None
        try:
            page = await self.new_page()

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(timeout * 1000),
            )

            # Wait for Cloudflare challenge to resolve
            await self._wait_for_cloudflare(page, timeout=timeout)

            html = await page.content()

            # Check offline markers
            html_lower = html.lower()
            for marker in _OFFLINE_MARKERS:
                if marker.lower() in html_lower:
                    log.debug(
                        "stealth_probe_dead",
                        url=url,
                        marker=marker,
                    )
                    return False

            log.debug("stealth_probe_alive", url=url)
            return True
        except Exception:
            log.debug("stealth_probe_error", url=url, exc_info=True)
            return False
        finally:
            if page is not None and not page.is_closed():
                await page.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _wait_for_cloudflare(
        self, page: Page, *, timeout: float = 10
    ) -> None:
        """Wait until the page title no longer contains CF challenge markers."""
        cf_title_markers = [m for m in _CF_MARKERS if " " in m or m[0].isupper()]
        # "Just a moment", "Attention Required"
        js_check = " && ".join(
            f"!t.includes('{m}')" for m in cf_title_markers
        )
        js = f"() => {{ const t = document.title; return {js_check}; }}"
        try:
            await page.wait_for_function(js, timeout=int(timeout * 1000))
        except Exception:  # noqa: BLE001
            pass  # proceed — page may still be usable
