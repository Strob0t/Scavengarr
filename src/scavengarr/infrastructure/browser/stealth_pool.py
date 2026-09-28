"""Patchright stealth context for Cloudflare bypass probing and fetching.

Runs in its own context on the shared Chromium of ``SharedBrowserPool``
(one browser process). Patchright removes the automation leaks
(``Runtime.enable``, automation launch flags) that Cloudflare detects.
Pages are created per-probe and closed immediately after.
Resource blocking (images, fonts, CSS, media) keeps navigation fast.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import structlog
from patchright.async_api import Browser, BrowserContext, Page, Request, Route

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


# fetch_text() retries: rate limits / overloaded origin, then give up
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_RETRY_BACKOFF_S: tuple[float, ...] = (2.0, 5.0, 10.0)

# In-page fetch for non-HTML responses (null on HTTP error)
_FETCH_RAW_JS = """async (url) => {
    const resp = await fetch(url, {credentials: "include"});
    return resp.ok ? await resp.text() : null;
}"""


# Requests a hoster's player makes for the stream: HLS/DASH manifests,
# progressive MP4 (VOE-style "master.txt" manifests included)
_MEDIA_URL_RE = re.compile(
    r"\.(?:m3u8|mpd|mp4)(?:$|\?)|/master\.txt(?:$|\?)", re.IGNORECASE
)
# capture_media(): wait this long for autoplay, then click the player up to
# _MEDIA_PLAY_CLICKS times (on ad-funded hosters the first click often only
# opens a popup), waiting _MEDIA_CLICK_WAIT_S after each click
_MEDIA_AUTOPLAY_WAIT_S = 3.0
_MEDIA_CLICK_WAIT_S = 5.0
_MEDIA_PLAY_CLICKS = 3
# Dead-file notices of player pages, matched against the visible text only
# (player scripts carry such strings as error templates)
_PLAYER_OFFLINE_TEXT: tuple[str, ...] = (
    "video not found",
    "file not found",
    "no such file",
    "no longer available",
    "has been removed",
    "has been deleted",
    "file was removed",
    "deleted by the owner",
)
_VISIBLE_TEXT_JS = (
    "() => document.title + '\\n' + (document.body ? document.body.innerText : '')"
)


@dataclass(frozen=True)
class CapturedMedia:
    """Stream URL requested by a hoster's player, with that request's Referer."""

    url: str
    referer: str | None


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


async def _allow_player_resources(route: Route) -> None:
    """Load a player page fully (layout decides where the play button is);
    only media downloads are cut, their URL is known once requested."""
    if route.request.resource_type == "media":
        await route.abort()
    else:
        await route.continue_()


async def _close_popup(popup: Page) -> None:
    """Close ad popups opened by clicks on a player."""
    try:
        await popup.close()
    except Exception:  # noqa: BLE001
        log.debug("stealth_popup_close_error", exc_info=True)


async def _shows_offline_notice(page: Page) -> bool:
    """True when the player page (title or visible text) says the file is gone.

    Unreadable pages (a navigation in flight) count as not offline.
    """
    try:
        text = await page.evaluate(_VISIBLE_TEXT_JS)
    except Exception:  # noqa: BLE001
        log.debug("stealth_page_text_unreadable", exc_info=True)
        return False
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _PLAYER_OFFLINE_TEXT)


async def _start_player(
    page: Page, found: asyncio.Future[CapturedMedia]
) -> CapturedMedia | None:
    """Wait for autoplay, then click the page centre (the player) until the
    player requests its stream, says the file is gone (some players tell
    only after the click) or the clicks are used up."""
    waits = (_MEDIA_AUTOPLAY_WAIT_S, *(_MEDIA_CLICK_WAIT_S,) * _MEDIA_PLAY_CLICKS)
    for attempt, wait_s in enumerate(waits):
        if attempt:
            size = page.viewport_size or {"width": 1280, "height": 720}
            await page.mouse.click(size["width"] / 2, size["height"] / 2)
        try:
            return await asyncio.wait_for(asyncio.shield(found), wait_s)
        except TimeoutError:
            pass
        if attempt and await _shows_offline_notice(page):
            log.info("stealth_capture_offline", url=page.url, clicks=attempt)
            return None
    return None


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

    async def _navigate(
        self,
        page: Page,
        url: str,
        *,
        wait_until: Literal["commit", "domcontentloaded"],
        timeout_ms: int,
        done: Callable[[], bool] = lambda: False,
    ) -> bool:
        """Navigate *page* to *url*, retrying rate limits / overloaded origins.

        Returns False on a permanent error status (not a challenge page).
        429/502/503/504 are retried after each backoff in ``_RETRY_BACKOFF_S``;
        the caller's fetch slot stays taken while waiting, which also throttles
        the other fetches. *done* short-circuits (and swallows the aborted
        navigation) once the caller has what it needs.
        """
        for backoff in (*_RETRY_BACKOFF_S, None):
            try:
                resp = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            except Exception:
                if done():
                    return True
                raise
            if done() or resp is None or resp.status < 400:
                return True
            if await is_challenge_page(page):
                return True
            log.info(
                "stealth_http_error", url=url, status=resp.status, retry_in_s=backoff
            )
            if backoff is None or resp.status not in _RETRY_STATUSES:
                return False
            await asyncio.sleep(backoff)
        return False  # pragma: no cover  (loop always returns)

    async def fetch_text(self, url: str, *, timeout: float) -> str | None:
        """Return the body of *url*, solving a Cloudflare challenge first.

        Implements ``BrowserFetcherPort``. HTML pages come back as rendered
        DOM; other types (JSON) are re-fetched in-page so the caller gets the
        raw body rather than Chrome's viewer markup. Same cookies and TLS
        fingerprint as the cleared page.
        """
        timeout_ms = int(timeout * 1000)
        async with self._fetch_sem:
            page: Page | None = None
            try:
                page = await self.new_page()
                if not await self._navigate(
                    page, url, wait_until="domcontentloaded", timeout_ms=timeout_ms
                ):
                    return None
                if not await solve_cloudflare(page, timeout_ms=timeout_ms):
                    return None
                return await read_when_settled(page, lambda: _read_body(page, url))
            except Exception:  # noqa: BLE001
                log.debug("stealth_fetch_error", url=url, exc_info=True)
                return None
            finally:
                if page is not None and not page.is_closed():
                    await page.close()

    async def capture_media(self, url: str, *, timeout: float) -> CapturedMedia | None:
        """Open *url*, start its player and return the stream URL it requests.

        For hosters whose stream URL exists only in the running player
        (token or proof-of-work flows such as Filemoon's "click play to
        verify you're a human") or whose embed page sits behind Cloudflare.
        The page loads with styles (the context blocks them) so the click
        on the page centre hits the play button; ad popups are closed.
        *timeout* bounds navigation and the Cloudflare challenge.
        """
        timeout_ms = int(timeout * 1000)
        async with self._fetch_sem:
            page: Page | None = None
            try:
                page = await self.new_page()
                found: asyncio.Future[CapturedMedia] = (
                    asyncio.get_running_loop().create_future()
                )

                def _on_request(request: Request) -> None:
                    if not found.done() and _MEDIA_URL_RE.search(request.url):
                        found.set_result(
                            CapturedMedia(request.url, request.headers.get("referer"))
                        )

                page.on("request", _on_request)
                page.on("popup", _close_popup)
                await page.route("**/*", _allow_player_resources)
                if not await self._navigate(
                    page,
                    url,
                    wait_until="domcontentloaded",
                    timeout_ms=timeout_ms,
                    done=found.done,
                ):
                    return None
                if not found.done():
                    if not await solve_cloudflare(page, timeout_ms=timeout_ms):
                        return None
                    if await _shows_offline_notice(page):
                        log.info("stealth_capture_offline", url=url)
                        return None
                media = await _start_player(page, found)
                log.debug("stealth_capture", url=url, found=media is not None)
                return media
            except Exception:  # noqa: BLE001
                log.debug("stealth_capture_error", url=url, exc_info=True)
                return None
            finally:
                if page is not None and not page.is_closed():
                    await page.close()

    async def resolve_redirect(self, url: str, *, timeout: float) -> str | None:
        """Return the first off-site URL *url* redirects to.

        Implements ``BrowserFetcherPort``. Listens to ``request`` events,
        which fire for every redirect hop (routes only see the first URL of a
        chain), and records the first navigation that leaves the origin host.
        Navigation waits only for ``commit``; the target may well be dead
        (e.g. a removed link container) — link validation decides that.
        """
        origin = urlparse(url).hostname
        targets: list[str] = []

        def _on_request(request: Request) -> None:
            if (
                not targets
                and request.is_navigation_request()
                and urlparse(request.url).hostname != origin
            ):
                targets.append(request.url)

        timeout_ms = int(timeout * 1000)
        async with self._fetch_sem:
            page: Page | None = None
            try:
                page = await self.new_page()
                page.on("request", _on_request)
                if not await self._navigate(
                    page,
                    url,
                    wait_until="commit",
                    timeout_ms=timeout_ms,
                    done=lambda: bool(targets),
                ):
                    return None
                # A challenge page redirects once solved; the listener sees it
                if not targets and await is_challenge_page(page):
                    await solve_cloudflare(page, timeout_ms=timeout_ms)
            except Exception:  # noqa: BLE001
                log.debug("stealth_redirect_error", url=url, exc_info=True)
            finally:
                if page is not None and not page.is_closed():
                    await page.close()
        return targets[0] if targets else None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def wait_for_cloudflare(self, page: Page, *, timeout: float = 10) -> bool:
        """Solve a Cloudflare challenge on *page* (Turnstile click included)."""
        return await solve_cloudflare(page, timeout_ms=int(timeout * 1000))
