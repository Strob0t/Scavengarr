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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from patchright.async_api import Browser, Playwright, async_playwright

from scavengarr.infrastructure.browser.display import resolve_headless
from scavengarr.infrastructure.browser.hardening import CHROMIUM_ARGS

log = structlog.get_logger(__name__)

# Chromium's memory grows with the pages it rendered (1.7 GB on the Pi);
# it restarts after this many pages, once none is open
_RECYCLE_AFTER_PAGES = 200


class SharedBrowserPool:
    """Manages a single shared Chromium browser for all Playwright plugins.

    Usage::

        pool = SharedBrowserPool(headless=False)

        # Called from use case (as background task while httpx plugins search):
        browser, pw = await pool.warmup()

        # Injected into PW plugins via set_shared_browser_task()

        # At shutdown:
        await pool.cleanup()
    """

    def __init__(self, *, headless: bool = False) -> None:
        self._headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._pages = 0
        # Operations holding the browser (lease()); set while it may be used,
        # cleared while it restarts
        self._active = 0
        self._ready = asyncio.Event()
        self._ready.set()
        self._restart: asyncio.Task[None] | None = None

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
            headless = resolve_headless(self._headless)
            self._browser = await self._pw.chromium.launch(
                headless=headless, args=list(CHROMIUM_ARGS)
            )
            log.info("shared_browser_launched", headless=headless)
            return self._browser, self._pw

    def note_page(self) -> None:
        """Count a page opened on the browser (see ``lease()``)."""
        self._pages += 1

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        """Hold the browser for one operation: it does not restart meanwhile.

        Waits while a restart runs. After ``_RECYCLE_AFTER_PAGES`` pages the
        last operation to end starts the restart in a task of its own. A
        restart when no context listed a page killed a page another slot was
        still opening, and it ran inside the finishing request's resolve
        timeout (code review, 2026-10-06).
        """
        while not self._ready.is_set():
            await self._ready.wait()
        self._active += 1
        try:
            yield
        finally:
            self._active -= 1
            if self._active == 0 and self._pages >= _RECYCLE_AFTER_PAGES:
                self._ready.clear()
                self._restart = asyncio.get_running_loop().create_task(self._recycle())

    async def _recycle(self) -> None:
        """Restart Chromium unless a page is open (one opened without a lease).

        The next ``warmup()`` launches a new one. Contexts on the old browser
        notice that it is closed and start over (stored clearance cookies
        are restored into new contexts).
        """
        try:
            async with self._lock:
                browser = self._browser
                if browser is None or any(ctx.pages for ctx in browser.contexts):
                    return
                pages, self._pages = self._pages, 0
                await self._close()
                log.info("shared_browser_recycled", pages=pages)
        finally:
            self._ready.set()

    async def cleanup(self) -> None:
        """Close the shared browser and Playwright instance."""
        if self._restart is not None and not self._restart.done():
            await self._restart
        await self._close()
        log.info("shared_browser_cleaned_up")

    async def _close(self) -> None:
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
