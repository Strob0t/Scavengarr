"""Shared base class for Playwright-based Python plugins.

Eliminates boilerplate that is duplicated across Playwright plugins:
browser lifecycle, context/page management, Cloudflare waiting,
domain verification, and cleanup.
"""

from __future__ import annotations

import asyncio

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from scavengarr.domain.plugins.base import SearchResult

from .constants import (
    DEFAULT_DOMAIN_CHECK_TIMEOUT,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_RESULTS,
    DEFAULT_USER_AGENT,
)


class PlaywrightPluginBase:
    """Shared base for Playwright-based Python plugins.

    Subclasses **must** set:
    - ``name``
    - ``provides`` (``"stream"`` | ``"download"`` | ``"both"``)
    - ``_domains`` (list with at least one domain string)

    Subclasses **must** override:
    - ``search()``
    """

    # --- Must be set by subclass ---
    name: str = ""
    provides: str = "download"

    # --- Overridable defaults ---
    version: str = "1.0.0"
    mode: str = "playwright"
    default_language: str = "de"

    _domains: list[str] = []  # noqa: RUF012
    _max_concurrent: int = DEFAULT_MAX_CONCURRENT
    _max_results: int = DEFAULT_MAX_RESULTS
    _user_agent: str = DEFAULT_USER_AGENT
    _headless: bool = True

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._domain_verified: bool = False
        self.base_url: str = f"https://{self._domains[0]}" if self._domains else ""
        self._log = structlog.get_logger(self.name or __name__)

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> Browser:
        """Launch Chromium browser if not already running."""
        if self._browser is None:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self._headless,
            )
            self._log.info(f"{self.name}_browser_launched")
        return self._browser

    async def _ensure_context(self) -> BrowserContext:
        """Create browser context with standard user-agent."""
        if self._context is None:
            browser = await self._ensure_browser()
            self._context = await browser.new_context(
                user_agent=self._user_agent,
                viewport={"width": 1280, "height": 720},
            )
        return self._context

    async def _ensure_page(self) -> Page:
        """Get or create a persistent page."""
        if self._page is None or self._page.is_closed():
            ctx = await self._ensure_context()
            self._page = await ctx.new_page()
        return self._page

    async def _new_page(self) -> Page:
        """Create a fresh page (caller is responsible for closing)."""
        ctx = await self._ensure_context()
        return await ctx.new_page()

    # ------------------------------------------------------------------
    # Domain verification
    # ------------------------------------------------------------------

    async def _verify_domain(self) -> None:
        """Find a working domain by navigating in the browser."""
        if self._domain_verified or len(self._domains) <= 1:
            self._domain_verified = True
            return

        page = await self._ensure_page()
        for domain in self._domains:
            url = f"https://{domain}/"
            try:
                resp = await page.goto(
                    url,
                    timeout=int(DEFAULT_DOMAIN_CHECK_TIMEOUT * 1000),
                    wait_until="domcontentloaded",
                )
                if resp and resp.status < 400:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    self._log.info(f"{self.name}_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{self._domains[0]}"
        self._domain_verified = True
        self._log.warning(
            f"{self.name}_no_domain_reachable",
            fallback=self._domains[0],
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def _fetch_page_html(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout: int = 30_000,
    ) -> str:
        """Navigate to *url* and return the page HTML.

        Returns an empty string on failure.
        """
        page = await self._new_page()
        try:
            resp = await page.goto(url, wait_until=wait_until, timeout=timeout)
            if resp and resp.status < 400:
                return await page.content()
            self._log.warning(
                f"{self.name}_page_error",
                url=url,
                status=resp.status if resp else None,
            )
            return ""
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                f"{self.name}_page_failed",
                url=url,
                error=str(exc),
            )
            return ""
        finally:
            await page.close()

    def _new_semaphore(self) -> asyncio.Semaphore:
        """Create a bounded semaphore for concurrent scraping."""
        return asyncio.Semaphore(self._max_concurrent)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        """Close page, context, browser, and Playwright."""
        if self._page is not None and not self._page.is_closed():
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        self._domain_verified = False

    # ------------------------------------------------------------------
    # Abstract search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search the site and return normalised results.

        Subclasses **must** override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.search() not implemented")
