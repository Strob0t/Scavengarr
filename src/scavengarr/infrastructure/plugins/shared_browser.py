"""Shared Chromium browser pool for Playwright plugins.

Manages a single Chromium process shared by all Playwright plugins.
Each plugin gets its own ``BrowserContext`` for isolation while
sharing the same underlying browser — saving ~1-2s startup per
additional Playwright plugin.

The pool supports concurrent ``warmup()`` calls via an asyncio lock:
the first caller launches Chromium, subsequent concurrent callers
wait and then receive the same instance.
"""

from __future__ import annotations

import asyncio

import structlog
from playwright.async_api import Browser, Playwright, async_playwright

log = structlog.get_logger(__name__)


class SharedBrowserPool:
    """Manages a single shared Chromium browser for all Playwright plugins.

    Usage::

        pool = SharedBrowserPool(headless=True)

        # Called from use case (as background task while httpx plugins search):
        browser, pw = await pool.warmup()

        # Injected into PW plugins via set_shared_browser_task()

        # At shutdown:
        await pool.cleanup()
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Whether the shared browser is currently connected."""
        return self._browser is not None and self._browser.is_connected()

    async def warmup(self) -> tuple[Browser, Playwright]:
        """Ensure Chromium is running, launching it if needed.

        Thread-safe via ``asyncio.Lock`` — concurrent calls wait for the
        first launch to complete, then return the same instance.

        If the browser has disconnected (crash, etc.), it is relaunched.
        """
        if self._browser is not None and self._browser.is_connected():
            assert self._pw is not None  # invariant: browser implies pw
            return self._browser, self._pw

        async with self._lock:
            # Double-check after acquiring lock
            if self._browser is not None and self._browser.is_connected():
                assert self._pw is not None  # invariant: browser implies pw
                return self._browser, self._pw

            # Clean up stale state if browser crashed
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:  # noqa: BLE001
                    log.debug("shared_browser_stale_pw_stop_error", exc_info=True)
                self._pw = None
                self._browser = None

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self._headless,
            )
            log.info("shared_browser_launched")
            return self._browser, self._pw

    async def cleanup(self) -> None:
        """Close the shared browser and Playwright instance."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                log.warning("shared_browser_close_error", exc_info=True)
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                log.warning("shared_pw_stop_error", exc_info=True)
            self._pw = None
        log.info("shared_browser_cleaned_up")
