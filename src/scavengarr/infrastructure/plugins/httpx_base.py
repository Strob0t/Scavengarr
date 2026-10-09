"""Shared base class for httpx-based Python plugins.

Eliminates boilerplate that is duplicated across 27+ plugins:
client lifecycle, domain verification, cleanup, safe fetch/parse,
and semaphore creation.

This base class lives in the *infrastructure* layer because it depends
on ``httpx`` and ``structlog``.  The *domain* layer only knows
``PluginProtocol``; plugins that inherit from ``HttpxPluginBase``
structurally satisfy that Protocol.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import EpisodeRef
from scavengarr.domain.plugins.base import PluginUnreachableError, SearchResult
from scavengarr.domain.ports.browser_fetcher import BrowserFetcherPort, BrowserSession
from scavengarr.domain.ports.cache import CachePort
from scavengarr.infrastructure.browser.cloudflare import is_cloudflare_challenge
from scavengarr.infrastructure.captcha.detect import (
    detect_challenge,
    detect_challenge_headers,
)

from .categories import category_matches
from .constants import (
    DEFAULT_CLIENT_TIMEOUT,
    DEFAULT_DOMAIN_CHECK_TIMEOUT,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_RESULTS,
    DEFAULT_USER_AGENT,
    search_max_results,
)

# Browser fallback budget per page: covers a Turnstile click + redirect
_BROWSER_FETCH_TIMEOUT_S = 30.0
# Same-host redirects followed by _resolve_redirect() before giving up
_MAX_REDIRECT_HOPS = 5
# How long a host that showed a Cloudflare challenge skips plain httpx
_CF_BLOCK_MEMO_S = 30 * 60
# A challenge this soon after httpx took over the browser's session means the
# site does not accept that session from httpx: its pages go to the browser
_SESSION_TRUST_S = 5 * 60
# A domain that only answered an error page or a challenge at the domain
# check serves this long, then the check runs again; a transient 429, 403
# or 404 at check time must not pin a domain for the process lifetime
_ANSWERING_DOMAIN_RECHECK_S = 5 * 60


def _forget_solve(solve: asyncio.Task[str | None]) -> None:
    """Drop a finished browser solve; its error was logged by the solve."""
    HttpxPluginBase._background_solves.discard(solve)
    if not solve.cancelled():
        solve.exception()  # retrieved: a cut request never awaits it


def _site_answers(resp: httpx.Response) -> bool:
    """The domain's site is up: an answer below 500 (an error page is still
    the site), or a challenge (up behind Cloudflare: kinoger answers 403
    with ``cf-mitigated: challenge``), read from the HEAD answer's headers
    by the health prober's rule (``detect_challenge_headers``)."""
    return (
        resp.status_code < 500
        or detect_challenge_headers(resp.status_code, resp.headers) is not None
    )


class HttpxPluginBase:
    """Shared base for httpx-based Python plugins.

    Subclasses **must** set:
    - ``name``
    - ``provides`` (``"stream"`` | ``"download"`` | ``"both"``)
    - ``_domains`` (list with at least one domain string)

    Subclasses **must** override:
    - ``search()`` (the abstract stub raises ``NotImplementedError``)

    Subclasses **may** override:
    - ``version``, ``mode``, ``languages``
    - ``_max_concurrent``, ``_max_results``, ``_timeout``
    - ``_user_agent``
    """

    # --- Shared HTTP client (set once, used by all instances) ---
    _shared_http_client: httpx.AsyncClient | None = None
    # --- Browser fallback for Cloudflare challenges (set once, optional) ---
    _browser_fetcher: BrowserFetcherPort | None = None
    # --- The app's cache (set once, optional): what a plugin keeps across
    # requests, e.g. the episode index of a series (episode_index.py) ---
    _cache: CachePort | None = None
    # host -> monotonic deadline: hosts that answered with a challenge go
    # straight to the browser (a doomed httpx request still counts against
    # the site's rate limit)
    _cf_blocked_until: dict[str, float] = {}  # noqa: RUF012  # shared on purpose
    # host -> the browser's User-Agent and when httpx took over the browser's
    # session for that site (its clearance cookie is valid with that UA only)
    _browser_user_agents: dict[str, str] = {}  # noqa: RUF012  # shared on purpose
    _session_adopted_at: dict[str, float] = {}  # noqa: RUF012  # shared on purpose
    # browser solves in flight (kept referenced: they outlive cut requests)
    _background_solves: set[asyncio.Task[str | None]] = set()  # noqa: RUF012

    # --- Must be set by subclass ---
    name: str = ""
    provides: str = "download"

    # --- Overridable defaults ---
    version: str = "1.0.0"
    mode: str = "httpx"
    languages: list[str] = ["de"]  # noqa: RUF012  # subclass overrides
    # Sites that front one database share a group name: a Stremio request
    # asks one of them (the next when its circuit breaker opens)
    mirror_group: str | None = None

    @property
    def default_language(self) -> str:
        """First language — backward-compatible property."""
        return self.languages[0]

    _domains: list[str] = []  # noqa: RUF012  # subclass overrides
    _max_concurrent: int = DEFAULT_MAX_CONCURRENT
    _max_results: int = DEFAULT_MAX_RESULTS
    _timeout: float = DEFAULT_CLIENT_TIMEOUT
    _user_agent: str = DEFAULT_USER_AGENT
    cache_ttl: int | None = None

    @classmethod
    def set_shared_http_client(cls, client: httpx.AsyncClient) -> None:
        """Inject a shared HTTP client for all httpx plugin instances.

        When set, plugins reuse this client instead of creating their
        own.  Per-plugin timeout and headers are applied per-request
        so each plugin's overrides still work.
        """
        cls._shared_http_client = client

    @staticmethod
    def set_browser_fetcher(fetcher: BrowserFetcherPort | None) -> None:
        """Inject the browser used when a site answers with a CF challenge.

        Set on the base class so every plugin sees it; ``None`` disables
        the fallback (``playwright.browser_fallback: false``).
        """
        HttpxPluginBase._browser_fetcher = fetcher
        HttpxPluginBase._cf_blocked_until.clear()
        HttpxPluginBase._browser_user_agents.clear()
        HttpxPluginBase._session_adopted_at.clear()

    @staticmethod
    def set_cache(cache: CachePort | None) -> None:
        """Inject the app's cache for every httpx plugin.

        A plugin keeps what it reads once for many requests under a key
        of its own (``aniworld:episodes:v1:<slug>``); ``None`` (tests,
        no cache) makes it read anew every time.
        """
        HttpxPluginBase._cache = cache

    def _cf_fetcher_for(self, url: str) -> BrowserFetcherPort | None:
        """The browser fetcher if *url*'s host recently showed a challenge."""
        fetcher = self._browser_fetcher
        host = urlparse(url).hostname or ""
        if fetcher is None or self._cf_blocked_until.get(host, 0.0) < time.monotonic():
            return None
        return fetcher

    def _mark_cf_blocked(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        self._cf_blocked_until[host] = time.monotonic() + _CF_BLOCK_MEMO_S

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._domain_verified: bool = False
        # Until when an answering-only domain (_use_domain, pinned=False)
        # serves without a new check
        self._domain_recheck_at: float = 0.0
        self.base_url: str = f"https://{self._domains[0]}" if self._domains else ""
        # The base URL before the site moved (_follow_site_move)
        self._moved_from: str | None = None
        self._log = structlog.get_logger(self.name or __name__)

    @property
    def effective_max_results(self) -> int:
        """Max results respecting caller context (e.g. Stremio limit)."""
        ctx = search_max_results.get(None)
        if ctx is not None:
            return min(ctx, self._max_results)
        return self._max_results

    # Whether a result labelled *accepted* answers a request for *requested*
    _category_matches = staticmethod(category_matches)

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Return an HTTP client, preferring the shared instance."""
        if self._client is not None:
            return self._client

        if self._shared_http_client is not None:
            self._client = self._shared_http_client
        else:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent},
            )
        return self._client

    async def _verify_domain(self) -> None:
        """Find and cache a working domain from the fallback list.

        Uses the *final* URL after redirects so that domains that
        redirect (e.g. ``aniworld.info`` → ``www.aniworld.info``)
        produce a correct ``base_url`` for subsequent requests. The first
        domain answering below 400 wins and stays for the process
        lifetime; without one, the first that answers at all
        (``_site_answers``: an error page, or a Cloudflare challenge the
        plugin's browser fallback solves) serves the searches of the next
        ``_ANSWERING_DOMAIN_RECHECK_S`` and is checked again then. Raises
        ``PluginUnreachableError`` when no domain answers; the next
        search checks again.
        """
        if self._domain_verified or len(self._domains) <= 1:
            self._domain_verified = True
            return
        if time.monotonic() < self._domain_recheck_at:
            return

        client = await self._ensure_client()
        answering: tuple[str, httpx.Response] | None = None
        for domain in self._domains:
            url = f"https://{domain}/"
            try:
                resp = await client.head(url, timeout=DEFAULT_DOMAIN_CHECK_TIMEOUT)
            except Exception:  # noqa: BLE001
                self._log.debug(f"{self.name}_domain_check_failed", domain=domain)
                continue
            if resp.status_code < 400:
                self._use_domain(domain, resp, pinned=True)
                return
            if answering is None and _site_answers(resp):
                answering = (domain, resp)

        if answering is not None:
            domain, resp = answering
            self._log.info(
                f"{self.name}_domain_answers", domain=domain, status=resp.status_code
            )
            self._use_domain(domain, resp, pinned=False)
            return
        self._log.warning(f"{self.name}_no_domain_reachable")
        raise PluginUnreachableError(self.name)

    def _use_domain(self, domain: str, resp: httpx.Response, *, pinned: bool) -> None:
        """Take the domain's final URL after any redirects (e.g. a ``www.``
        prefix) as ``base_url``: for the process lifetime when *pinned* (the
        domain works), else for ``_ANSWERING_DOMAIN_RECHECK_S`` (it only
        answered an error page or a challenge)."""
        self.base_url = str(resp.url).rstrip("/")
        self._domain_verified = pinned
        self._domain_recheck_at = (
            0.0 if pinned else time.monotonic() + _ANSWERING_DOMAIN_RECHECK_S
        )
        self._log.info(
            f"{self.name}_domain_found", domain=domain, resolved=resp.url.host
        )

    async def cleanup(self) -> None:
        """Close httpx client (skip if it is the shared instance)."""
        if self._client is not None and self._client is not self._shared_http_client:
            await self._client.aclose()
        self._client = None
        self._domain_verified = False
        self._domain_recheck_at = 0.0

    def _follow_site_move(self, resp: httpx.Response) -> None:
        """Make a permanent move of the site to another host the base URL.

        Only a chain of permanent redirects (301/308) from the base host
        counts. hdfilme answered every search with a 301 from
        hdfilme.cafe to hdfilme.ceo: one more round trip per request.
        """
        history = resp.history
        if not history or any(r.status_code not in (301, 308) for r in history):
            return
        old = httpx.URL(self.base_url).netloc
        if history[0].url.netloc != old or resp.url.netloc == old:
            return
        if self._moved_from is None:
            self._moved_from = self.base_url
        self.base_url = f"{resp.url.scheme}://{resp.url.netloc.decode('ascii')}"
        self._log.info(
            f"{self.name}_site_moved",
            old=old.decode("ascii"),
            new=resp.url.netloc.decode("ascii"),
        )

    def _undo_site_move(self, url: str) -> None:
        """Go back to the base URL the site moved from when *url*, on the
        host it moved to, gets no answer.

        hdfilme moves on every few days, and its old host redirects to the
        newest one; a new host that died kept every search on it until a
        restart.
        """
        moved_from = self._moved_from
        host = httpx.URL(url).netloc
        if moved_from is None or host != httpx.URL(self.base_url).netloc:
            return
        self.base_url, self._moved_from = moved_from, None
        self._log.warning(
            f"{self.name}_site_move_undone",
            host=host.decode("ascii"),
            base_url=moved_from,
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def _safe_fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        context: str = "",
        **kwargs: Any,
    ) -> httpx.Response | None:
        """Fetch *url* with structured error logging.

        Sends the plugin's timeout and User-Agent like ``_fetch_text()``
        (``_request_kwargs()``: the browser's User-Agent to a site whose
        session httpx took over); the caller's *headers* go on top.
        Returns ``None`` on failure instead of raising.
        """
        client = await self._ensure_client()

        defaults = self._request_kwargs(client, url)
        if "timeout" in defaults:
            kwargs.setdefault("timeout", defaults["timeout"])
        if "headers" in defaults:
            kwargs["headers"] = {**defaults["headers"], **(kwargs.get("headers") or {})}

        try:
            handler = getattr(client, method.lower(), client.get)
            resp = await handler(url, **kwargs)
            resp.raise_for_status()
            self._follow_site_move(resp)
            return resp
        except httpx.TimeoutException:
            self._undo_site_move(url)
            self._log.warning(
                f"{self.name}_timeout",
                url=url,
                context=context,
            )
        except httpx.HTTPStatusError as exc:
            self._log.warning(
                f"{self.name}_http_error",
                url=url,
                status=exc.response.status_code,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, httpx.TransportError):
                self._undo_site_move(url)
            self._log.warning(
                f"{self.name}_fetch_error",
                url=url,
                error=str(exc),
                context=context,
            )
        return None

    async def _fetch_text(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        context: str = "",
        headers: dict[str, str] | None = None,
    ) -> str | None:
        """GET *url* and return the body text (``None`` on failure).

        When the site answers with a Cloudflare challenge and a browser
        fetcher is injected, the same URL (query string included) is loaded
        through the browser instead, and later requests to the site go on
        with the browser's session (see ``_fetch_via_browser()``).  Without
        a fetcher this behaves like a plain GET with ``_safe_fetch()``-style
        logging.  A host in the browser memo goes straight to the browser.
        *headers* go with the httpx request (the browser sends its own).
        """
        memo_fetcher = self._cf_fetcher_for(url)
        if memo_fetcher is not None:
            full_url = str(httpx.URL(url, params=params)) if params else url
            return await memo_fetcher.fetch_text(
                full_url, timeout=_BROWSER_FETCH_TIMEOUT_S
            )

        client = await self._ensure_client()
        kwargs = self._request_kwargs(client, url)
        if params:
            kwargs["params"] = params
        if headers:
            kwargs["headers"] = {**kwargs.get("headers", {}), **headers}

        try:
            resp = await client.get(url, **kwargs)
        except httpx.TimeoutException:
            self._undo_site_move(url)
            self._log.warning(f"{self.name}_timeout", url=url, context=context)
            return None
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, httpx.TransportError):
                self._undo_site_move(url)
            self._log.warning(
                f"{self.name}_fetch_error", url=url, error=str(exc), context=context
            )
            return None

        if resp.status_code < 400:
            self._follow_site_move(resp)
            return resp.text

        challenge = detect_challenge(resp.status_code, resp.text, resp.headers)
        fetcher = self._browser_fetcher
        if fetcher is not None and challenge == "cloudflare_page":
            return await self._fetch_via_browser(
                fetcher, str(resp.url), context=context, challenge=challenge
            )

        self._log.warning(
            f"{self.name}_http_error",
            url=url,
            status=resp.status_code,
            context=context,
            challenge=challenge,
        )
        return None

    async def _fetch_via_browser(
        self,
        fetcher: BrowserFetcherPort,
        url: str,
        *,
        context: str,
        challenge: str,
    ) -> str | None:
        """Load *url* in the browser and let httpx go on with its session.

        The browser passes the site's challenge once (seconds; on a Raspberry
        Pi several); every further page through it would cost that again.
        The site's cookies and the browser's User-Agent pass the same check
        through httpx, so later requests carry them. The host stays in the
        browser memo while the browser works (parallel requests do not try
        httpx), and for good when httpx meets a challenge despite a session
        taken over less than ``_SESSION_TRUST_S`` ago: that site binds its
        clearance to the browser. The solve runs on when the Stremio deadline
        cuts the request that started it, so the next request gets the
        session (on a Raspberry Pi under load a solve took 20 s).
        """
        host = urlparse(url).hostname or ""
        self._mark_cf_blocked(url)
        adopted_at = self._session_adopted_at.get(host)
        if adopted_at is not None and time.monotonic() - adopted_at < _SESSION_TRUST_S:
            self._log.info(
                f"{self.name}_browser_session_rejected", url=url, context=context
            )
            return await fetcher.fetch_text(url, timeout=_BROWSER_FETCH_TIMEOUT_S)

        self._log.info(
            f"{self.name}_browser_fallback",
            url=url,
            context=context,
            challenge=challenge,
        )
        solve = asyncio.create_task(self._solve_and_adopt(fetcher, url))
        self._background_solves.add(solve)
        solve.add_done_callback(_forget_solve)
        return await asyncio.shield(solve)

    async def _solve_and_adopt(
        self, fetcher: BrowserFetcherPort, url: str
    ) -> str | None:
        """Load *url* in the browser, then take over the browser's session."""
        try:
            body = await fetcher.fetch_text(url, timeout=_BROWSER_FETCH_TIMEOUT_S)
            if body is None:
                return None
            session = await fetcher.session(url)
            if isinstance(session, BrowserSession):
                await self._adopt_browser_session(url, session)
            return body
        except Exception:
            # Nobody may await a solve whose request was cut: log it here
            self._log.warning(
                f"{self.name}_browser_solve_failed", url=url, exc_info=True
            )
            raise

    async def _adopt_browser_session(self, url: str, session: BrowserSession) -> None:
        """Send the browser's cookies and User-Agent with requests to *url*'s site."""
        await self._use_browser_session(url, session.cookies)
        host = urlparse(url).hostname or ""
        self._browser_user_agents[host] = session.user_agent
        self._session_adopted_at[host] = time.monotonic()
        self._cf_blocked_until.pop(host, None)
        self._log.info(
            f"{self.name}_browser_session_adopted",
            host=host,
            cookies=sorted(session.cookies),
        )

    def _parse_json_text(self, body: str | None, context: str = "") -> dict | None:
        """Decode a JSON object from ``_fetch_text()`` (``None`` on failure)."""
        if body is None:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._log.warning(f"{self.name}_invalid_json", context=context)
            return None
        return data if isinstance(data, dict) else None

    async def _resolve_own_links(
        self,
        links: list[dict[str, str]],
        sem: asyncio.Semaphore | None = None,
    ) -> list[dict[str, str]]:
        """Replace link-out URLs on the plugin's own host by their targets.

        Sites like filmfans/serienfans hand out ``/external/<hash>`` URLs that
        redirect to the hoster or link container. Behind Cloudflare nobody
        downstream (link validation, JDownloader) can follow them, so they are
        resolved here; links that do not resolve are dropped. Links on other
        hosts are kept as they are. At most ``_max_concurrent`` redirects run
        at once (*sem* shares that bound across calls).
        """
        own_host = urlparse(self.base_url).hostname
        bound = sem or self._new_semaphore()

        async def _one(link: dict[str, str]) -> dict[str, str] | None:
            url = link.get("link", "")
            if urlparse(url).hostname != own_host:
                return link
            async with bound:
                target = await self._resolve_redirect(url, context="link")
            return {**link, "link": target} if target else None

        resolved = await asyncio.gather(*(_one(link) for link in links))
        return [link for link in resolved if link is not None]

    async def _resolve_result_links(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Apply ``_resolve_own_links()`` to final results (after capping).

        Results left without any usable link are dropped. Call this on the
        results actually returned so only their links cost a round trip.
        """
        sem = self._new_semaphore()

        async def _one(result: SearchResult) -> SearchResult | None:
            links = result.download_links or [
                {"hoster": "", "link": result.download_link}
            ]
            resolved = await self._resolve_own_links(links, sem)
            if not resolved:
                return None
            result.download_link = resolved[0]["link"]
            result.download_links = resolved
            return result

        updated = await asyncio.gather(*(_one(r) for r in results))
        return [r for r in updated if r is not None]

    def _request_kwargs(
        self, client: httpx.AsyncClient, url: str = ""
    ) -> dict[str, Any]:
        """Per-plugin timeout and User-Agent when using the shared client.

        Requests to a site whose browser session httpx took over send the
        browser's User-Agent instead (*url* names the site).
        """
        browser_ua = self._browser_user_agents.get(urlparse(url).hostname or "")
        if client is not self._shared_http_client:
            return {"headers": {"User-Agent": browser_ua}} if browser_ua else {}
        return {
            "timeout": httpx.Timeout(self._timeout),
            "headers": {"User-Agent": browser_ua or self._user_agent},
        }

    async def _use_browser_session(self, url: str, cookies: dict[str, str]) -> None:
        """Send *cookies* (a browser's session) with later requests to *url*'s site.

        For sites that trust a session once it passed a gate in the browser
        (e.g. a Turnstile widget before link-outs). The site's own cookies in
        the client are dropped first, so no stale session is sent alongside.
        """
        client = await self._ensure_client()
        host = urlparse(url).hostname or ""
        jar = client.cookies.jar
        for cookie in list(jar):
            if cookie.domain.lstrip(".") == host:
                jar.clear(cookie.domain, cookie.path, cookie.name)
        for name, value in cookies.items():
            client.cookies.set(name, value, domain=host)

    async def _resolve_redirect(
        self, url: str, *, context: str = "", referer: str = ""
    ) -> str | None:
        """Return the off-site target of a link-out URL (``/external/<hash>``).

        Follows same-host redirects (up to ``_MAX_REDIRECT_HOPS``) and returns
        the first ``Location`` on another host, without loading it. When the
        site answers with a Cloudflare challenge, the injected browser fetcher
        resolves it instead (directly, if the host showed a challenge within
        ``_CF_BLOCK_MEMO_S``).  ``None`` when *url* does not leave its host.
        *referer* is sent as ``Referer``: some sites redirect a link-out only
        when it is opened from the page that lists it.
        """
        memo_fetcher = self._cf_fetcher_for(url)
        if memo_fetcher is not None:
            return await memo_fetcher.resolve_redirect(
                url, timeout=_BROWSER_FETCH_TIMEOUT_S
            )

        client = await self._ensure_client()
        kwargs = self._request_kwargs(client, url)
        if referer:
            base_headers = kwargs.get("headers")
            headers = dict(base_headers) if isinstance(base_headers, dict) else {}
            kwargs["headers"] = {**headers, "Referer": referer}
        current = url
        for _ in range(_MAX_REDIRECT_HOPS):
            try:
                resp = await client.get(current, follow_redirects=False, **kwargs)
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    f"{self.name}_redirect_error",
                    url=current,
                    error=str(exc),
                    context=context,
                )
                return None
            location = resp.headers.get("location") if resp.is_redirect else None
            if location:
                target = urljoin(current, location)
                if urlparse(target).hostname != urlparse(url).hostname:
                    return target
                current = target
                continue
            fetcher = self._browser_fetcher
            if fetcher is not None and is_cloudflare_challenge(
                resp.status_code, resp.text
            ):
                self._mark_cf_blocked(current)
                return await fetcher.resolve_redirect(
                    current, timeout=_BROWSER_FETCH_TIMEOUT_S
                )
            self._log.debug(
                f"{self.name}_no_redirect",
                url=current,
                status=resp.status_code,
                context=context,
            )
            return None
        return None

    def _safe_parse_json(
        self,
        response: httpx.Response,
        context: str = "",
    ) -> dict | list | None:
        """Parse JSON response with structured error logging."""
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            self._log.warning(
                f"{self.name}_invalid_json",
                url=str(response.url),
                context=context,
            )
            return None

    def _new_semaphore(self) -> asyncio.Semaphore:
        """Create a bounded semaphore for concurrent detail scraping."""
        return asyncio.Semaphore(self._max_concurrent)

    # ------------------------------------------------------------------
    # Per-request isolation (passthrough for httpx plugins)
    # ------------------------------------------------------------------

    async def isolated_search(
        self,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
        episode_ref: EpisodeRef | None = None,
    ) -> list[SearchResult]:
        """Run search — httpx plugins need no context isolation.

        *episode_ref* (the runner gives it to a plugin that locates
        episodes only, see ``PluginProtocol``) reaches ``search()`` as a
        keyword; without one ``search()`` is called as before the
        reference existed, so a plugin without the keyword keeps working.
        """
        # A plugin that locates episodes takes the reference as a keyword
        # of its search(); the base signature has none
        search: Callable[..., Awaitable[list[SearchResult]]] = self.search
        extra: dict[str, EpisodeRef] = {}
        if episode_ref is not None:
            extra["episode_ref"] = episode_ref
        return await search(query, category, season=season, episode=episode, **extra)

    # ------------------------------------------------------------------
    # Abstract search (subclass must implement)
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
