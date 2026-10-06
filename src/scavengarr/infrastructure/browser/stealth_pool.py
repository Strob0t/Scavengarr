"""Patchright stealth context for Cloudflare bypass fetching and capture.

Runs in its own context on the shared Chromium of ``SharedBrowserPool``
(one browser process). Patchright removes the automation leaks
(``Runtime.enable``, automation launch flags) that Cloudflare detects.
Pages are created per fetch and closed immediately after.
Resource blocking (images, fonts, CSS, media) keeps navigation fast.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import structlog
from patchright.async_api import Browser, BrowserContext, Page, Request, Route

from scavengarr.domain.ports.browser_fetcher import BrowserSession, ClickThrough
from scavengarr.infrastructure.browser.hardening import block_heavy_resources
from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.browser.turnstile import (
    WIDGET_FORM,
    is_challenge_page,
    pass_turnstile_widget,
    read_when_settled,
    solve_cloudflare,
)

if TYPE_CHECKING:
    from scavengarr.infrastructure.browser.clearance_store import ClearanceStore
    from scavengarr.infrastructure.browser.shared_browser import SharedBrowserPool

log = structlog.get_logger(__name__)


# fetch_text() retries: rate limits / overloaded origin, then give up
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_RETRY_BACKOFF_S: tuple[float, ...] = (2.0, 5.0, 10.0)

# In-page fetch for non-HTML responses and URLs that refuse a page load
# (null on HTTP error); it sends the page's URL as Referer
_FETCH_RAW_JS = """async (url) => {
    const resp = await fetch(url, {credentials: "include"});
    return resp.ok ? await resp.text() : null;
}"""


# Requests a hoster's player makes for the stream: HLS/DASH manifests,
# progressive MP4 (VOE-style "master.txt" manifests included)
_MEDIA_URL_RE = re.compile(
    r"\.(?:m3u8|mpd|mp4)(?:$|\?)|/master\.txt(?:$|\?)", re.IGNORECASE
)
# ...but not seek-preview playlists (e.g. vixeo's thumbnails.m3u8 of JPEGs)
_NOT_MEDIA_RE = re.compile(r"thumbnail|sprite|preview", re.IGNORECASE)
# capture_media(): wait this long for autoplay, then click the player up to
# _MEDIA_PLAY_CLICKS times (on ad-funded hosters the first click often only
# opens a popup), waiting _MEDIA_CLICK_WAIT_S after each click
# click_through(): bound for the click itself, poll interval for the target
_CLICK_TIMEOUT_MS = 5_000
_CLICK_POLL_MS = 300
# ...and the time a submitted gate gets for its redirect, past the timeout:
# on a Raspberry Pi behind a VPN the gate's widget took 28 of 30 s and the
# redirect after its form came too late (2026-10-04)
_SUBMIT_REDIRECT_S = 10.0
# Ad layers some sites put over the page (s.to: random class names, laid out
# after a delay) take the clicks: Playwright then waits until its click
# timeout ("… subtree intercepts pointer events"). Only the targets take
# pointer events, so the user-like click reaches them. A Turnstile iframe in
# a closed shadow root inherits the value of its host, a target's descendant
_TARGET_ONLY_CSS = (
    "* {{ pointer-events: none !important; }} "
    ":is({targets}), :is({targets}) * {{ pointer-events: auto !important; }}"
)

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


async def _allow_player_resources(route: Route) -> None:
    """Load a player page fully (layout decides where the play button is);
    only media downloads are cut, their URL is known once requested."""
    if route.request.resource_type == "media":
        await route.abort()
    else:
        await route.continue_()


async def _click_clear_of_layers(page: Page, selector: str) -> None:
    """Click the first match of *selector*; no layer over it takes the click.

    The rule stays until the page closes: the layers cover the gate's widget
    that the click may bring up as well, so the widget's form is a target too.
    """
    targets = f"{selector}, {WIDGET_FORM}"
    await page.add_style_tag(content=_TARGET_ONLY_CSS.format(targets=targets))
    await page.locator(selector).first.click(timeout=_CLICK_TIMEOUT_MS)


async def _wait_for_target(page: Page, targets: list[str], deadline: float) -> None:
    """Poll until *targets* gets the link-out's target, passing a gate's widget.

    A gate whose form went out gets ``_SUBMIT_REDIRECT_S`` for its redirect,
    also past *deadline*: the widget may have used up the time.
    """
    passed = False
    while not targets and (left := deadline - time.monotonic()) > 0:
        if not passed:
            passed = await pass_turnstile_widget(page, timeout_ms=int(left * 1000))
            if passed:
                deadline = max(deadline, time.monotonic() + _SUBMIT_REDIRECT_S)
        await page.wait_for_timeout(_CLICK_POLL_MS)


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
    for plugins and resolvers) in its own persistent context, so Cloudflare
    clearance cookies survive between fetches. Every operation holds one
    page of *pages* (``PageGate``).

    Usage::

        pool = StealthPool(browser_pool=shared_pool, timeout_ms=15_000)
        html = await pool.fetch_text("https://example.com/embed/abc", timeout=15)
        await pool.cleanup()
    """

    def __init__(
        self,
        *,
        browser_pool: SharedBrowserPool,
        timeout_ms: int = 15_000,
        pages: PageGate | None = None,
        clearance_store: ClearanceStore | None = None,
    ) -> None:
        self._browser_pool = browser_pool
        # Solved challenges survive restarts (cf_clearance, __ddg* cookies)
        self._clearance_store = clearance_store
        self._timeout_ms = timeout_ms
        # Bounds the pages of all operations (headful pages are heavy)
        self._pages = pages or PageGate(limit=2)
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._user_agent: str | None = None

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
            self._context = await self._browser.new_context(service_workers="block")
            self._user_agent = None  # a relaunched browser may be another version

            # Block heavy resources on all pages in this context
            await self._context.route("**/*", block_heavy_resources)
            if self._clearance_store is not None:
                await self._clearance_store.restore(self._context)

            log.info("stealth_pool_started")
            return self._context

    async def _remember(self, page: Page) -> None:
        """Keep the clearance cookies of a page that passed its challenge."""
        if self._clearance_store is not None:
            await self._clearance_store.remember(page.context)

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
        self._browser_pool.note_page()
        return await ctx.new_page()

    async def _close(self, page: Page | None) -> None:
        """Close *page* (the operation's lease lets the browser restart)."""
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
        the caller's page stays taken while waiting, which also throttles
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
        fingerprint as the cleared page. A URL that answers the page load
        with an error (no challenge) is asked once more by in-page fetch:
        an API may serve only requests from its site's pages (moflix, Laravel
        Sanctum: 401 without the site's Referer, which a page load lacks
        unless a challenge's reload sends it).
        """
        timeout_ms = int(timeout * 1000)
        async with (
            self._pages.page("plugin", timeout=timeout),
            self._browser_pool.lease(),
        ):
            page: Page | None = None
            try:
                page = await self.new_page()
                if not await self._navigate(
                    page, url, wait_until="domcontentloaded", timeout_ms=timeout_ms
                ):
                    return await page.evaluate(_FETCH_RAW_JS, url)
                if not await solve_cloudflare(page, timeout_ms=timeout_ms):
                    return None
                await self._remember(page)
                return await read_when_settled(page, lambda: _read_body(page, url))
            except Exception:  # noqa: BLE001
                log.debug("stealth_fetch_error", url=url, exc_info=True)
                return None
            finally:
                await self._close(page)

    async def session(self, url: str) -> BrowserSession | None:
        """Return the context's cookies for *url* and the browser's User-Agent.

        Implements ``BrowserFetcherPort``. The persistent context keeps the
        cookies that ``fetch_text()`` collected while passing the site's
        challenge.
        """
        async with self._browser_pool.lease():
            context = await self._ensure_context()
            cookies: dict[str, str] = {}
            for cookie in await context.cookies(url):
                name, value = cookie.get("name"), cookie.get("value")
                if name and value is not None:
                    cookies[name] = value
            if not cookies:
                return None
            return BrowserSession(
                cookies=cookies, user_agent=await self._browser_user_agent()
            )

    async def _browser_user_agent(self) -> str:
        """The User-Agent the browser sends (read once from a blank page)."""
        if self._user_agent is None:
            page = await self.new_page()
            try:
                self._user_agent = str(await page.evaluate("navigator.userAgent"))
            finally:
                await page.close()
        return self._user_agent

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
        async with (
            self._pages.page("capture", timeout=timeout),
            self._browser_pool.lease(),
        ):
            page: Page | None = None
            try:
                page = await self.new_page()
                found: asyncio.Future[CapturedMedia] = (
                    asyncio.get_running_loop().create_future()
                )

                def _on_request(request: Request) -> None:
                    # Manifests/MP4 by URL; extension-less CDN URLs (DoodStream
                    # ``…~abc?token=``) by the video element's request type
                    if (
                        not found.done()
                        and not _NOT_MEDIA_RE.search(request.url)
                        and (
                            request.resource_type == "media"
                            or _MEDIA_URL_RE.search(request.url)
                        )
                    ):
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
                    await self._remember(page)
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
                await self._close(page)

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
        async with (
            self._pages.page("plugin", timeout=timeout),
            self._browser_pool.lease(),
        ):
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
                await self._close(page)
        return targets[0] if targets else None

    async def click_through(
        self, page_url: str, selector: str, *, timeout: float
    ) -> ClickThrough | None:
        """Click a link-out on *page_url*; return its target and the site's cookies.

        Implements ``BrowserFetcherPort``. The target is the first navigation
        that a request to the page's own host redirected off-site: the
        link-out's redirect, also after a gate's form POST. Ad frames that
        load other hosts by themselves do not count. A Turnstile widget the
        click brings up is passed and its form submitted.
        """
        site = urlparse(page_url).hostname
        targets: list[str] = []

        def _on_request(request: Request) -> None:
            source = request.redirected_from
            if (
                not targets
                and request.is_navigation_request()
                and source is not None
                and urlparse(source.url).hostname == site
                and urlparse(request.url).hostname not in (None, site)
            ):
                targets.append(request.url)

        deadline = time.monotonic() + timeout
        timeout_ms = int(timeout * 1000)
        async with (
            self._pages.page("plugin", timeout=timeout),
            self._browser_pool.lease(),
        ):
            page: Page | None = None
            try:
                page = await self.new_page()
                page.on("request", _on_request)
                page.on("popup", _close_popup)
                # Full layout: the click must hit the element like a user's
                await page.route("**/*", _allow_player_resources)
                if not await self._navigate(
                    page, page_url, wait_until="domcontentloaded", timeout_ms=timeout_ms
                ):
                    return None
                if not await solve_cloudflare(page, timeout_ms=timeout_ms):
                    return None
                await _click_clear_of_layers(page, selector)
                await _wait_for_target(page, targets, deadline)
                if not targets:
                    log.info("stealth_click_through_no_target", url=page_url)
                    return None
                await self._remember(page)
                cookies = await page.context.cookies(page_url)
                return ClickThrough(
                    url=targets[0],
                    cookies={
                        name: c.get("value", "")
                        for c in cookies
                        if (name := c.get("name"))
                    },
                )
            except Exception:  # noqa: BLE001
                log.debug("stealth_click_through_error", url=page_url, exc_info=True)
                return None
            finally:
                await self._close(page)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def wait_for_cloudflare(self, page: Page, *, timeout: float = 10) -> bool:
        """Solve a Cloudflare challenge on *page* (Turnstile click included)."""
        return await solve_cloudflare(page, timeout_ms=int(timeout * 1000))
