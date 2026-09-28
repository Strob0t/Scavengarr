"""Shared base class for Playwright-based Python plugins.

Eliminates boilerplate that is duplicated across Playwright plugins:
browser lifecycle, context/page management, Cloudflare waiting,
domain verification, and cleanup.
"""

from __future__ import annotations

import asyncio
from contextvars import Token
from typing import Any

import structlog
from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    async_playwright,
)

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.browser.display import resolve_headless
from scavengarr.infrastructure.browser.turnstile import (
    is_challenge_page,
    solve_cloudflare,
)

from .constants import (
    DEFAULT_DOMAIN_CHECK_TIMEOUT,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_RESULTS,
    DEFAULT_USER_AGENT,
    search_max_results,
)

# Statuses worth retrying in _fetch_page_html (rate limits, overloaded origin)
_RETRY_STATUSES = frozenset({429, 502, 503, 504})


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
    languages: list[str] = ["de"]  # noqa: RUF012  # subclass overrides

    @property
    def default_language(self) -> str:
        """First language — backward-compatible property."""
        return self.languages[0]

    _domains: list[str] = []  # noqa: RUF012
    _max_concurrent: int = DEFAULT_MAX_CONCURRENT
    _max_results: int = DEFAULT_MAX_RESULTS
    # HTTP-only UA (httpx side requests). Browser contexts keep Patchright's
    # real UA unless _browser_user_agent is set: a forced UA disagrees with
    # the client hints (sec-ch-ua) and gets flagged by Cloudflare.
    _user_agent: str = DEFAULT_USER_AGENT
    _browser_user_agent: str | None = None
    # False = headful when a display exists (needed for Cloudflare Turnstile),
    # see resolve_headless().
    _headless: bool = False
    cache_ttl: int | None = None

    # --- Abort image/font/CSS requests (on by default) ---
    # Patchright (not playwright-stealth) handles anti-bot evasion at the
    # driver level; its Console domain is disabled, so page.on("console")
    # never fires.
    _block_resources: bool = True

    # --- Cloudflare / navigation timeouts ---
    # Covers an interactive Turnstile click; only spent while a challenge shows.
    _cf_timeout_ms: int = 30_000
    _networkidle_timeout_ms: int = 10_000

    # --- Request isolation ---
    # When True, search() is serialized with a lock instead of using
    # per-request BrowserContext isolation (for plugins that rely on
    # persistent page state, e.g. streamworld, moflix).
    _serialize_search: bool = False

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._domain_verified: bool = False
        self.base_url: str = f"https://{self._domains[0]}" if self._domains else ""
        self._log = structlog.get_logger(self.name or __name__)
        # Shared browser support: when set, _ensure_browser() calls
        # pool.warmup() instead of launching a new Chromium process.
        self._shared_pool: object | None = None
        self._owns_browser: bool = True
        # Lock for serialized search (used when _serialize_search=True)
        self._search_lock = asyncio.Lock()

    @property
    def effective_max_results(self) -> int:
        """Max results respecting caller context (e.g. Stremio limit)."""
        ctx = search_max_results.get(None)
        if ctx is not None:
            return min(ctx, self._max_results)
        return self._max_results

    @staticmethod
    def _category_matches(requested: int | None, accepted: int) -> bool:
        """Check whether *requested* is compatible with *accepted*.

        Torznab categories follow a ``X000`` parent / ``X0YY`` child
        scheme.  When the caller asks for a parent (e.g. 5000 = any TV)
        every child in that range (5000-5999) should match.  A specific
        request like 5070 only matches 5070 exactly.

        Returns ``True`` when the plugin should proceed with the search.
        """
        if requested is None:
            return True
        if requested == accepted:
            return True
        if requested % 1000 == 0 and accepted // 1000 == requested // 1000:
            return True
        return False

    # ------------------------------------------------------------------
    # Shared browser pool injection
    # ------------------------------------------------------------------

    def set_shared_pool(self, pool: object) -> None:
        """Inject a :class:`SharedBrowserPool` reference.

        When set, ``_ensure_browser()`` calls ``pool.warmup()`` to
        obtain the shared Chromium browser instead of launching its
        own process.  ``cleanup()`` will only close the context and
        page — the browser is managed by the pool.

        Typed as ``object`` to avoid importing infrastructure types
        into the base class.  The pool must have an async
        ``warmup() -> tuple[Browser, Playwright]`` method.
        """
        self._shared_pool = pool
        self._owns_browser = False

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    async def _ensure_browser(self) -> Browser:
        """Return a connected Chromium browser, launching if needed.

        When a shared pool is set (via ``set_shared_pool``), calls
        ``pool.warmup()`` to obtain the shared browser instead of
        launching a dedicated Chromium process.  This allows multiple
        Playwright plugins to share a single browser with separate
        contexts.

        Includes a single retry with 1 s backoff when the launch fails
        (e.g. transient OOM in a container).
        """
        # Check if existing browser is still connected
        if self._browser is not None and not self._browser.is_connected():
            self._log.warning(f"{self.name}_browser_disconnected")
            self._browser = None
            self._pw = None
            self._context = None
            self._page = None

        if self._browser is None:
            if self._shared_pool is not None:
                # Use shared browser from pool
                self._browser, self._pw = await self._shared_pool.warmup()
                self._owns_browser = False
                self._log.info(f"{self.name}_using_shared_browser")
            else:
                # Standalone: launch own Playwright + Chromium (1 retry)
                self._browser = await self._launch_standalone(retries=1)
                self._owns_browser = True
                self._log.info(f"{self.name}_browser_launched")
        return self._browser

    async def _launch_standalone(self, *, retries: int = 1) -> Browser:
        """Launch a standalone Chromium, retrying once on failure."""
        last_exc: Exception | None = None
        for attempt in range(1 + retries):
            try:
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(
                    headless=resolve_headless(self._headless)
                )
                self._pw = pw
                return browser
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                # Clean up partial state from failed attempt
                if self._pw is None:
                    # pw might have been assigned before launch failed
                    try:
                        await pw.stop()
                    except Exception:  # noqa: BLE001
                        self._log.debug("pw_stop_cleanup_failed", exc_info=True)
                if attempt < retries:
                    self._log.warning(
                        f"{self.name}_browser_launch_retry",
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                    await asyncio.sleep(1)
        raise last_exc  # type: ignore[misc]

    async def _ensure_context(self) -> BrowserContext:
        """Create the browser context (no forced user-agent by default).

        When a per-request ``BrowserContext`` is set in
        :data:`~scavengarr.infrastructure.plugins.context_vars.request_browser_context`,
        that context is returned instead of the singleton — unless
        ``_serialize_search`` is True (persistent-page plugins).

        When ``_block_resources`` is True, aborts heavy resources
        (images, fonts, CSS) to speed up navigation.
        """
        # Per-request isolation: if a ContextVar context was set by
        # isolated_search(), prefer it over the singleton.
        if not self._serialize_search:
            from .context_vars import request_browser_context

            req_ctx = request_browser_context.get(None)
            if req_ctx is not None:
                return req_ctx

        if self._context is None:
            browser = await self._ensure_browser()
            self._context = await browser.new_context(**self._context_options())
            await self._configure_context(self._context)
        return self._context

    def _context_options(self) -> dict[str, Any]:
        """Keyword arguments for ``browser.new_context()``."""
        options: dict[str, Any] = {"viewport": {"width": 1280, "height": 720}}
        if self._browser_user_agent is not None:
            options["user_agent"] = self._browser_user_agent
        return options

    async def _configure_context(self, ctx: BrowserContext) -> None:
        """Apply per-context settings shared by singleton and isolated contexts."""
        if self._block_resources:
            await ctx.route(
                "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,css}",
                lambda route: route.abort(),
            )

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
    # Cloudflare handling
    # ------------------------------------------------------------------

    async def _wait_for_cloudflare(self, page: Page) -> bool:
        """Solve a Cloudflare challenge on *page* (Turnstile click included).

        Returns ``True`` if the page is usable, ``False`` if the challenge
        is still shown after ``_cf_timeout_ms``.
        """
        return await solve_cloudflare(page, timeout_ms=self._cf_timeout_ms)

    async def _passes_cloudflare(self, page: Page, resp: Response | None) -> bool:
        """Accept a navigation, solving a Cloudflare challenge if one is shown.

        Challenges arrive as 403/503, so an error status only fails the
        navigation when the page is not a challenge page.
        """
        if (
            resp is not None
            and resp.status >= 400
            and not await is_challenge_page(page)
        ):
            return False
        return await self._wait_for_cloudflare(page)

    async def _navigate_and_wait(
        self,
        page: Page,
        url: str,
        *,
        wait_for_cf: bool = True,
        wait_for_idle: bool = True,
    ) -> bool:
        """Navigate to *url*, optionally wait for CF and ``networkidle``.

        Combines the three steps that almost every Playwright plugin
        repeats: ``goto`` → Cloudflare wait → ``networkidle``.

        Returns ``True`` when the page loaded with status < 400,
        ``False`` otherwise.  Goto exceptions propagate to the caller.
        """
        resp = await page.goto(url, wait_until="domcontentloaded")
        if wait_for_cf:
            ok = await self._passes_cloudflare(page, resp)
        else:
            ok = not (resp and resp.status >= 400)
        if not ok:
            self._log.warning(
                f"{self.name}_navigate_error",
                url=url,
                status=resp.status if resp else None,
            )
            return False

        if wait_for_idle:
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=self._networkidle_timeout_ms,
                )
            except Exception:  # noqa: BLE001
                self._log.debug("networkidle_timeout", url=url)

        return True

    # ------------------------------------------------------------------
    # Domain verification
    # ------------------------------------------------------------------

    async def _verify_domain(self) -> None:
        """Find a working domain by navigating in the browser.

        A domain counts as reachable when it answers with status < 400 or
        with a Cloudflare challenge that gets solved.  Otherwise the next
        candidate is tried.
        """
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
                if resp and await self._passes_cloudflare(page, resp):
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    self._log.info(f"{self.name}_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                self._log.debug(f"{self.name}_domain_check_failed", domain=domain)
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
        wait_for_idle: bool = True,
        retry_backoff_s: tuple[float, ...] = (),
    ) -> str:
        """Navigate to *url* and return the page HTML ("" on failure).

        Solves a Cloudflare challenge if one is shown, optionally waits for
        ``networkidle``.  A transient failure (429/502/503/504 or no
        response) is retried once per entry in *retry_backoff_s*, sleeping
        that long first; other errors (e.g. 404) fail at once.
        """
        page = await self._new_page()
        try:
            for backoff in (*retry_backoff_s, None):
                resp = await page.goto(url, wait_until=wait_until, timeout=timeout)
                if await self._passes_cloudflare(page, resp):
                    break
                self._log.warning(
                    f"{self.name}_page_error",
                    url=url,
                    status=resp.status if resp else None,
                    retry_in_s=backoff,
                )
                transient = resp is None or resp.status in _RETRY_STATUSES
                if backoff is None or not transient:
                    return ""
                await asyncio.sleep(backoff)
            if wait_for_idle:
                try:
                    await page.wait_for_load_state(
                        "networkidle",
                        timeout=self._networkidle_timeout_ms,
                    )
                except Exception:  # noqa: BLE001
                    self._log.debug("networkidle_timeout", url=url)
            return await page.content()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                f"{self.name}_page_failed",
                url=url,
                error=str(exc),
            )
            return ""
        finally:
            if not page.is_closed():
                await page.close()

    def _new_semaphore(self) -> asyncio.Semaphore:
        """Create a bounded semaphore for concurrent scraping."""
        return asyncio.Semaphore(self._max_concurrent)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        """Close page, context, and optionally browser + Playwright.

        When using a shared browser (via ``set_shared_browser_task``),
        only the context and page are closed — the browser and Playwright
        instances are managed by the external ``SharedBrowserPool``.
        """
        if self._page is not None and not self._page.is_closed():
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._owns_browser:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None
        else:
            # Shared browser — just clear references
            self._browser = None
            self._pw = None
        self._domain_verified = False

    # ------------------------------------------------------------------
    # Per-request isolation
    # ------------------------------------------------------------------

    async def _prepare_context(self, ctx: BrowserContext) -> None:
        """Hook for subclasses to inject cookies/state into a fresh context.

        Called by :meth:`isolated_search` after creating a per-request
        BrowserContext.  Override this in authenticated plugins to copy
        session cookies into *ctx* via ``ctx.add_cookies()``.
        """

    async def isolated_search(
        self,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Run *search()* with a per-request BrowserContext.

        For serialized plugins (``_serialize_search = True``), search
        is guarded by a lock instead of creating an isolated context.

        For all other plugins, a fresh BrowserContext is created,
        set into the ContextVar, and torn down after search completes.
        """
        if self._serialize_search:
            async with self._search_lock:
                return await self.search(
                    query, category, season=season, episode=episode
                )

        from .context_vars import request_browser_context

        browser = await self._ensure_browser()
        ctx = await browser.new_context(**self._context_options())
        token: Token[BrowserContext | None] | None = None
        try:
            await self._configure_context(ctx)
            await self._prepare_context(ctx)
            token = request_browser_context.set(ctx)
            return await self.search(query, category, season=season, episode=episode)
        finally:
            if token is not None:
                request_browser_context.reset(token)
            for page in ctx.pages:
                if not page.is_closed():
                    await page.close()
            await ctx.close()

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
