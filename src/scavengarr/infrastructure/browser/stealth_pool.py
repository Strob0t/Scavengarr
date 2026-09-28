"""Patchright stealth context for Cloudflare bypass probing and fetching.

Runs in its own context on the shared Chromium of ``SharedBrowserPool``
(one browser process). Patchright removes the automation leaks
(``Runtime.enable``, automation launch flags) that Cloudflare detects.
Pages are created per-probe and closed immediately after.
Resource blocking (images, fonts, CSS, media) keeps navigation fast.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from patchright.async_api import Browser, BrowserContext, Page, Route

from scavengarr.infrastructure.browser.turnstile import (
    is_challenge_page,
    read_when_settled,
    solve_cloudflare,
)

if TYPE_CHECKING:
    from scavengarr.infrastructure.browser.shared_browser import SharedBrowserPool

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
    'class="removed"',
    'class="deleted"',
    'class="fake-signup"',
)


# In-page fetch for non-HTML responses (null on HTTP error)
_FETCH_RAW_JS = """async (url) => {
    const resp = await fetch(url, {credentials: "include"});
    return resp.ok ? await resp.text() : null;
}"""


async def _read_body(page: Page, url: str) -> str | None:
    """Rendered DOM for HTML, raw in-page fetch for other types (JSON)."""
    if await page.evaluate("() => document.contentType") == "text/html":
        return await page.content()
    return await page.evaluate(_FETCH_RAW_JS, url)


async def _block_resources(route: Route) -> None:
    """Abort requests for heavy resource types."""
    if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


class StealthPool:
    """Lazy-init Patchright browser pool for Cloudflare bypass probing.

    Runs on the Chromium of :class:`SharedBrowserPool` (one browser process
    for plugins and probes) in its own persistent context, so Cloudflare
    clearance cookies survive between probes.

    Usage::

        pool = StealthPool(browser_pool=shared_pool, timeout_ms=15_000)
        alive = await pool.probe_url("https://example.com/embed/abc")
        await pool.cleanup()
    """

    def __init__(
        self,
        *,
        browser_pool: SharedBrowserPool,
        timeout_ms: int = 15_000,
        fetch_concurrency: int = 2,
    ) -> None:
        self._browser_pool = browser_pool
        self._timeout_ms = timeout_ms
        # Bounds fetch_text() pages (RAM budget: headful pages are heavy)
        self._fetch_sem = asyncio.Semaphore(fetch_concurrency)
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _context_is_usable(self) -> bool:
        return (
            self._context is not None
            and self._browser is not None
            and self._browser.is_connected()
        )

    async def _ensure_context(self) -> BrowserContext:
        """Create the context on the shared browser (double-check lock).

        Recreated when the shared browser was relaunched after a crash.
        """
        if self._context_is_usable():
            assert self._context is not None
            return self._context
        async with self._lock:
            if self._context_is_usable():
                assert self._context is not None
                return self._context

            self._browser, _ = await self._browser_pool.warmup()
            self._context = await self._browser.new_context()

            # Block heavy resources on all pages in this context
            await self._context.route("**/*", _block_resources)

            log.info("stealth_pool_started")
            return self._context

    async def cleanup(self) -> None:
        """Close the stealth context — idempotent.

        The browser belongs to :class:`SharedBrowserPool` and is closed there.
        """
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                log.debug("stealth_context_close_error", exc_info=True)
            self._context = None
        self._browser = None

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
            await self.wait_for_cloudflare(page, timeout=timeout)

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

    async def fetch_text(self, url: str, *, timeout: float) -> str | None:
        """Return the body of *url*, solving a Cloudflare challenge first.

        Implements ``BrowserFetcherPort``. HTML pages come back as rendered
        DOM; other types (JSON) are re-fetched in-page so the caller gets the
        raw body rather than Chrome's viewer markup. Same cookies and TLS
        fingerprint as the cleared page.
        """
        async with self._fetch_sem:
            page: Page | None = None
            try:
                page = await self.new_page()
                resp = await page.goto(
                    url, wait_until="domcontentloaded", timeout=int(timeout * 1000)
                )
                if (
                    resp is not None
                    and resp.status >= 400
                    and not await is_challenge_page(page)
                ):
                    log.info("stealth_fetch_http_error", url=url, status=resp.status)
                    return None
                if not await solve_cloudflare(page, timeout_ms=int(timeout * 1000)):
                    return None
                return await read_when_settled(page, lambda: _read_body(page, url))
            except Exception:  # noqa: BLE001
                log.debug("stealth_fetch_error", url=url, exc_info=True)
                return None
            finally:
                if page is not None and not page.is_closed():
                    await page.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def wait_for_cloudflare(self, page: Page, *, timeout: float = 10) -> bool:
        """Solve a Cloudflare challenge on *page* (Turnstile click included)."""
        return await solve_cloudflare(page, timeout_ms=int(timeout * 1000))
