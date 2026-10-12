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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import structlog
from patchright.async_api import Browser, BrowserContext, Page, Request, Route
from patchright.async_api import Error as PlaywrightError

from scavengarr.domain.ports.browser_fetcher import BrowserSession, ClickThrough
from scavengarr.infrastructure.browser.hardening import block_heavy_resources
from scavengarr.infrastructure.browser.page_gate import (
    ChallengeMark,
    PageBusy,
    PageGate,
    ResolutionChallenge,
    challenge_mark,
)
from scavengarr.infrastructure.browser.turnstile import (
    WIDGET_FORM,
    is_challenge_page,
    pass_turnstile_widget,
    read_when_settled,
    solve_cloudflare,
)
from scavengarr.infrastructure.captcha.detect import challenge_frame

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
# click_through(): bound for the click itself (capture_media()'s look-up of
# the player it clicks too), poll interval for the target
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
# A capture with a stream keeps its hosts' state (ClearanceStore), at most
# once per host in this interval: the serialization covers the whole context
_KEEP_INTERVAL_S = 600.0
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


def _site(url: str) -> str:
    """The host's last two labels: ``n1mwq.org`` for ``a.n1mwq.org``."""
    host = (urlparse(url).hostname or "").lower()
    return ".".join(host.split(".")[-2:])


# Cloudflare's challenge platform: a Turnstile widget the page loads from it
# must run, or the page never passes its challenge. By exact host, not the
# site cloudflare.com: cdnjs.cloudflare.com serves any library
_CHALLENGE_HOST = "challenges.cloudflare.com"


def _foreign_script(resource_type: str, url: str, frame_urls: Iterable[str]) -> bool:
    """Whether a request is a script of a site that is neither the page's nor
    one of its frames' (a player's own scripts come from its page's or its
    frame's site, an ad tag from the ad network's). Scripts of
    ``_CHALLENGE_HOST`` always pass."""
    if resource_type != "script":
        return False
    if (urlparse(url).hostname or "").lower() == _CHALLENGE_HOST:
        return False
    return _site(url) not in {_site(frame_url) for frame_url in frame_urls}


def _challenge_of(
    request: Request, named: re.Pattern[str] | None
) -> ResolutionChallenge | None:
    """The challenge *request* shows: a captcha provider's frame, or a
    request the resolver names as its hoster's own captcha (*named*)."""
    if request.resource_type == "document" and (kind := challenge_frame(request.url)):
        return kind
    if named is not None and named.search(request.url):
        return "hoster_captcha"
    return None


def _capture_listener(
    found: asyncio.Future[CapturedMedia],
    mark: ChallengeMark | None,
    named: re.Pattern[str] | None,
) -> Callable[[Request], None]:
    """A capture's request listener: the first stream request settles
    *found*, a challenge (``_challenge_of``) marks the hoster resolution's
    *mark*."""

    def _on_request(request: Request) -> None:
        if mark is not None and (kind := _challenge_of(request, named)):
            mark.note(kind)
        # Manifests/MP4 by URL; extension-less CDN URLs (DoodStream
        # ``…~abc?token=``) by the video element's request type
        if (
            not found.done()
            and not _NOT_MEDIA_RE.search(request.url)
            and (request.resource_type == "media" or _MEDIA_URL_RE.search(request.url))
        ):
            found.set_result(CapturedMedia(request.url, request.headers.get("referer")))

    return _on_request


async def _allow_own_player_resources(route: Route) -> None:
    """``_allow_player_resources`` for a capture that runs the page's own
    scripts only: a script of another site than the page's and its frames'
    is aborted as the client's block. Filemoon's player frame loads an ad
    tag that turns every click into a popup, so the player never starts."""
    request = route.request
    if request.resource_type == "script":
        try:
            frame_urls = [frame.url for frame in request.frame.page.frames]
        except PlaywrightError:
            frame_urls = None  # a service worker's request: no frame to judge
        if frame_urls is not None and _foreign_script(
            request.resource_type, request.url, frame_urls
        ):
            log.debug("stealth_capture_script_blocked", domain=_site(request.url))
            await route.abort("blockedbyclient")
            return
    await _allow_player_resources(route)


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


async def _click_point(page: Page, player: str | None) -> tuple[float, float]:
    """Where a click starts the player: the centre of the *player* element
    when the page lays it out, else the page centre (an embed page is the
    player)."""
    box = None
    if player:
        element = page.locator(player).first
        if await element.count():
            box = await element.bounding_box(timeout=_CLICK_TIMEOUT_MS)
    if box:
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    size = page.viewport_size or {"width": 1280, "height": 720}
    return size["width"] / 2, size["height"] / 2


async def _start_player(
    page: Page, found: asyncio.Future[CapturedMedia], player: str | None
) -> CapturedMedia | None:
    """Wait for autoplay, then click the player (``_click_point``) until it
    requests its stream, says the file is gone (some players tell only
    after the click) or the clicks are used up."""
    waits = (_MEDIA_AUTOPLAY_WAIT_S, *(_MEDIA_CLICK_WAIT_S,) * _MEDIA_PLAY_CLICKS)
    for attempt, wait_s in enumerate(waits):
        if attempt:
            await page.mouse.click(*await _click_point(page, player))
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
        # The capture timeout of the resolvers (navigation plus challenge):
        # stremio.probe_stealth_timeout_seconds; the httpx plugins' fallback
        # passes its own budget to the other operations
        self._timeout_ms = timeout_ms
        # Bounds the pages of all operations (headful pages are heavy)
        self._pages = pages or PageGate(limit=2)
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock = asyncio.Lock()
        self._user_agent: str | None = None
        # The capture states being kept, and when each host's was last
        self._keep_tasks: set[asyncio.Task[None]] = set()
        self._kept: dict[str, float] = {}

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
            self._context = await self._new_context(self._browser)
            self._user_agent = None  # a relaunched browser may be another version

            # Block heavy resources on all pages in this context
            await self._context.route("**/*", block_heavy_resources)

            log.info("stealth_pool_started")
            return self._context

    async def warmup(self) -> None:
        """Launch the browser and create the context, with the kept state, at
        the app's start, so the first capture does not wait for the launch.

        A failure is logged and leaves the launch to the first capture.
        """
        try:
            await self._ensure_context()
        except Exception as exc:  # noqa: BLE001  (the first capture launches it)
            log.warning("browser_warmup_failed", error=type(exc).__name__)

    async def _new_context(self, browser: Browser) -> BrowserContext:
        """A context started from the kept clearance cookies and capture
        states (localStorage and IndexedDB can be set only at creation)."""
        state = None
        if self._clearance_store is not None:
            state = await self._clearance_store.creation_state()
        if state is not None:
            try:
                return await browser.new_context(
                    service_workers="block", storage_state=state
                )
            except Exception as exc:  # noqa: BLE001  (costs a challenge, not a capture)
                log.warning("capture_state_restore_failed", error=type(exc).__name__)
        return await browser.new_context(service_workers="block")

    async def _remember(self, page: Page) -> None:
        """Keep the clearance cookies of a page that passed its challenge."""
        if self._clearance_store is not None:
            await self._clearance_store.remember(page.context)

    async def cleanup(self) -> None:
        """Close the stealth context — idempotent.

        The browser belongs to :class:`SharedBrowserPool` and is closed there.
        """
        for task in self._keep_tasks:
            task.cancel()
        await asyncio.gather(*self._keep_tasks, return_exceptions=True)
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
        try:
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
        except PageBusy:
            log.info("stealth_page_busy", url=url)
            return None

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

    async def capture_media(
        self,
        url: str,
        *,
        timeout: float | None = None,
        player: str | None = None,
        own_scripts_only: bool = False,
        challenge_requests: re.Pattern[str] | None = None,
    ) -> CapturedMedia | None:
        """Open *url*, start its player and return the stream URL it requests.

        For hosters whose stream URL exists only in the running player
        (token or proof-of-work flows such as Filemoon's "click play to
        verify you're a human") or whose embed page sits behind Cloudflare.
        The page loads with styles (the context blocks them) so the click
        on the page centre hits the play button; ad popups are closed.
        *player* (a CSS selector) names the element to click instead, on a
        page that shows its player in a frame below a header (Filemoon's
        watch page); the page centre stands in while it is not laid out.
        *timeout* bounds navigation and the Cloudflare challenge; without
        one the pool's own applies (``stremio.probe_stealth_timeout_seconds``,
        the resolvers' capture). The player clicks after it are bounded by
        the pool's own waits. *own_scripts_only* aborts the scripts of other
        sites than the page's and its frames' (``_allow_own_player_resources``):
        an ad tag that takes the clicks. A captcha provider's frame and a
        request that *challenge_requests* matches (the hoster's own captcha)
        mark the hoster resolution the capture runs in (``challenge_mark``),
        as an interstitial does.
        """
        if timeout is None:
            timeout = self._timeout_ms / 1000
        timeout_ms = int(timeout * 1000)
        # Playwright calls the request listener outside this task's context
        mark = challenge_mark.get()
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
                page.on("request", _capture_listener(found, mark, challenge_requests))
                page.on("popup", _close_popup)
                await page.route(
                    "**/*",
                    _allow_own_player_resources
                    if own_scripts_only
                    else _allow_player_resources,
                )
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
                media = await _start_player(page, found, player)
                log.debug("stealth_capture", url=url, found=media is not None)
                if media is not None:
                    self._keep_state(page)
                return media
            except Exception:  # noqa: BLE001
                log.debug("stealth_capture_error", url=url, exc_info=True)
                return None
            finally:
                await self._close(page)

    def _keep_state(self, page: Page) -> None:
        """Keep the state of *page*'s host and its frames' hosts in the
        background (the resolution does not wait), each at most once per
        ``_KEEP_INTERVAL_S``."""
        if self._clearance_store is None:
            return
        now = time.monotonic()
        hosts = {
            host
            for frame in page.frames
            if (host := urlparse(frame.url).hostname)
            and now - self._kept.get(host, -_KEEP_INTERVAL_S) >= _KEEP_INTERVAL_S
        }
        if not hosts:
            return
        self._kept.update(dict.fromkeys(hosts, now))
        task = asyncio.create_task(self._save_state(page.context, sorted(hosts)))
        self._keep_tasks.add(task)
        task.add_done_callback(self._keep_tasks.discard)

    async def _save_state(self, context: BrowserContext, hosts: list[str]) -> None:
        assert self._clearance_store is not None
        try:
            state = await context.storage_state(indexed_db=True)
            await self._clearance_store.keep_capture_state(hosts, state)
        except Exception as exc:  # noqa: BLE001  (costs a challenge after a restart)
            for host in hosts:
                self._kept.pop(host, None)  # the next stream tries again
            log.warning(
                "capture_state_save_failed", hosts=hosts, error=type(exc).__name__
            )

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
        try:
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
        except PageBusy:
            log.info("stealth_page_busy", url=url)
            return None
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
        try:
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
                        page,
                        page_url,
                        wait_until="domcontentloaded",
                        timeout_ms=timeout_ms,
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
                    log.debug(
                        "stealth_click_through_error", url=page_url, exc_info=True
                    )
                    return None
                finally:
                    await self._close(page)
        except PageBusy:
            log.info("stealth_page_busy", url=page_url)
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def wait_for_cloudflare(self, page: Page, *, timeout: float = 10) -> bool:
        """Solve a Cloudflare challenge on *page* (Turnstile click included)."""
        return await solve_cloudflare(page, timeout_ms=int(timeout * 1000))
