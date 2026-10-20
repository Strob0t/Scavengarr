"""Registry that dispatches hoster URL resolution to per-hoster resolvers."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import fields, replace
from functools import partial
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import (
    ResolvedStream,
    StreamQuality,
    quality_from_resolution,
)
from scavengarr.domain.ports.hoster_resolver import (
    ClientBoundResolverPort,
    HosterResolverPort,
)
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.browser.page_gate import (
    ChallengeMark,
    PageBusy,
    PageGate,
    ResolutionChallenge,
    challenge_mark,
    work_clock,
)
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers._domain import extract_domain
from scavengarr.infrastructure.hoster_resolvers._verify import (
    PlaybackCheck,
    check_playable,
)
from scavengarr.infrastructure.hoster_resolvers.challenge_cooldown import (
    ChallengeCooldown,
)

log = structlog.get_logger(__name__)

# Cache TTLs for resolver results
_CACHE_TTL_ALIVE = 3600  # 1 hour for successful resolutions
_CACHE_TTL_DEAD = 900  # 15 minutes for failed resolutions
_CACHE_TTL_REDIRECT = 3600  # 1 hour for redirect mappings

# Evict expired entries every N resolve() calls
_EVICT_INTERVAL = 1000

# Maximum number of entries in each cache (result + redirect)
_MAX_CACHE_SIZE = 10_000

# Telemetry name of content-type probes (URLs without a hoster resolver)
_DIRECT = "direct"

# Most hosters without a resolver that are counted (unresolved_hosts)
_MAX_UNRESOLVED = 1000

# Streaming playlists, which plugins sometimes hand out directly. Not file
# suffixes: hoster pages end in the file name (streamtape /v/<id>/x.mp4)
_PLAYLIST_SUFFIXES = (".m3u8", ".mpd")
# The content types an HLS playlist comes with: the registered one, RFC 8216's
# audio/mpegurl, and the older x- ones (SRF's and funk's CDNs send
# application/x-mpegURL)
_HLS_CONTENT_TYPES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
)

# A capture the resolve bound cut after a challenge runs on for this long
# (_complete): with production's bound of 10 s, 30 s of work in all, past the
# production host's cold captures of 13.3 to 17.8 s (2026-10-11)
_COMPLETION_S = 20.0


def _is_playlist_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(_PLAYLIST_SUFFIXES)


def _measured(stream: ResolvedStream, check: PlaybackCheck) -> ResolvedStream:
    """*stream* with what its playback check read: the resolution's quality
    when the resolver named none or a lower one, and the file's size."""
    measured = quality_from_resolution(check.width, check.height)
    return replace(
        stream,
        quality=max(stream.quality, measured),
        size_bytes=check.size_bytes or stream.size_bytes,
    )


def _stamped(stream: ResolvedStream) -> ResolvedStream:
    """*stream* with the time of its resolution: the cache answers with it
    for an hour, and a stored link is fresh by it."""
    return stream if stream.resolved_at else replace(stream, resolved_at=time.time())


_STREAM_FIELDS = frozenset(f.name for f in fields(ResolvedStream))


def _plain(stream: ResolvedStream) -> dict[str, Any]:
    """*stream* as builtins only (``export_state``): the snapshot names no
    class, so moving one cannot make it unreadable. Not ``asdict``: six
    times slower at the cache's 10,000 entries."""
    data = {name: getattr(stream, name) for name in _STREAM_FIELDS}
    data["quality"] = int(stream.quality)
    return data


def _from_plain(data: dict[str, Any]) -> ResolvedStream:
    """:func:`_plain`'s inverse; a field another version had and this one
    lacks is dropped, one this version added takes its default."""
    known = {name: value for name, value in data.items() if name in _STREAM_FIELDS}
    known["quality"] = StreamQuality(known.get("quality", StreamQuality.UNKNOWN))
    return ResolvedStream(**known)


def _started(
    resolve: Callable[[], Awaitable[ResolvedStream | None]],
    clock: asyncio.Timeout,
    mark: ChallengeMark,
) -> asyncio.Task[ResolvedStream | None]:
    """*resolve* as a task under *clock* (``work_clock``) and *mark*
    (``challenge_mark``). Awaited through ``asyncio.shield``, the bound does
    not cancel it, so a challenged capture can run on past the cut."""
    clock_token = work_clock.set(clock)
    mark_token = challenge_mark.set(mark)
    try:
        return asyncio.ensure_future(resolve())
    finally:
        work_clock.reset(clock_token)
        challenge_mark.reset(mark_token)


def _cancels() -> int:
    """The cancel requests pending on the running task (``cancelling()``)."""
    task = asyncio.current_task()
    return 0 if task is None else task.cancelling()


async def _cancelled(task: asyncio.Task[Any]) -> None:
    """Cancel *task* and wait for its cleanup (a capture closes its page),
    whatever it ends with."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class _CacheEntry[T]:
    """Time-bounded cache entry (resolver results, redirect targets); a
    result keeps the name of the resolver that produced it."""

    __slots__ = ("expires_at", "resolver", "value")

    def __init__(self, value: T, ttl: float, resolver: str = "") -> None:
        self.value = value
        self.resolver = resolver
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class HosterResolverRegistry:
    """Dispatches hoster URL resolution to the appropriate resolver.

    Falls back to content-type probing when no specific resolver is registered.
    Caches resolution outcomes and redirect mappings in-memory. With a
    *circuit_breaker*, a resolver whose resolutions keep running into the
    timeout or giving unplayable streams is skipped for a while (keyed by
    resolver name, so a hoster's mirror domains share it). A resolution
    whose capture met a challenge ends as ``challenge`` instead: the
    resolver's links wait out a challenge cooldown (``ChallengeCooldown``),
    and the breaker does not count it. A capture the bound cut after its
    challenge runs on in the background and leaves its stream in the cache
    (``_complete``), on a page of the stealth browser's *pages* (a page
    stays free for the others). Every resolution is recorded in *telemetry*
    by resolver name (``direct`` for a probe).
    """

    def __init__(
        self,
        resolvers: list[HosterResolverPort] | None = None,
        http_client: httpx.AsyncClient | None = None,
        resolve_timeout: float = 15.0,
        verify_playback: bool = False,
        circuit_breaker: PluginCircuitBreaker | None = None,
        telemetry: TelemetryPort = NO_TELEMETRY,
        pages: PageGate | None = None,
    ) -> None:
        self._resolvers: dict[str, HosterResolverPort] = {}
        self._domain_map: dict[str, HosterResolverPort] = {}
        self._host_map: dict[str, HosterResolverPort] = {}
        self._http_client = http_client
        self._resolve_timeout = resolve_timeout
        self._circuit_breaker = circuit_breaker
        self._telemetry = telemetry
        self._pages = pages
        # Resolver results must also pass check_playable (needs http_client)
        self._verify_playback = verify_playback and http_client is not None
        self._result_cache: dict[str, _CacheEntry[ResolvedStream | None]] = {}
        self._redirect_cache: dict[str, _CacheEntry[str]] = {}
        # Resolutions and redirects cached (export_state's content)
        self._changes = 0
        self._resolve_count = 0
        # When a request last looked up a link of each resolver (demand_age)
        self._demand: dict[str, float] = {}
        # Half-open probes run on when their request is cut (see _resolve_with)
        self._probes: set[asyncio.Task[ResolvedStream | None]] = set()
        self._challenges = ChallengeCooldown()
        # Challenged captures that run on past the bound, by resolver
        # (_hand_over): the completion and the capture it awaits; and the
        # completions by the cache key of their link (_joined)
        self._completions: dict[
            str,
            tuple[
                asyncio.Task[ResolvedStream | None],
                asyncio.Task[ResolvedStream | None],
            ],
        ] = {}
        self._completing: dict[str, asyncio.Task[ResolvedStream | None]] = {}
        # Probes of links no resolver claims, by hoster (unresolved_hosts)
        self._unresolved: Counter[str] = Counter()
        for resolver in resolvers or []:
            self.register(resolver)

    def register(self, resolver: HosterResolverPort) -> None:
        """Register a resolver for a specific hoster.

        If the resolver exposes a ``supported_domains`` property, each
        domain is also mapped so that URL-based dispatch finds the
        resolver even when the URL domain differs from the resolver name
        (e.g. ``filelions`` → vidhide resolver). A ``supported_hosts``
        property claims full host names instead, for hosts whose
        second-level name other hosts share (``kinoger.pw`` is a Vidara
        player, ``kinoger.ru`` a redirect to VOE). A domain or host claimed
        twice stays with the first resolver (the composition registers
        specific resolvers before the generic XFS/DDL ones) and is logged.
        """
        self._resolvers[resolver.name] = resolver
        domains: frozenset[str] | None = getattr(resolver, "supported_domains", None)
        hosts: frozenset[str] | None = getattr(resolver, "supported_hosts", None)
        for claims, mapping in ((domains, self._domain_map), (hosts, self._host_map)):
            for domain in claims or ():
                existing = mapping.get(domain)
                if existing is not None and existing is not resolver:
                    log.warning(
                        "hoster_domain_conflict",
                        domain=domain,
                        kept=existing.name,
                        ignored=resolver.name,
                    )
                    continue
                mapping[domain] = resolver
        log.debug("hoster_resolver_registered", hoster=resolver.name)

    def _resolver_for(self, url: str, name: str) -> HosterResolverPort | None:
        """Resolver for *url*: its host's claim, then its second-level *name*."""
        host = (urlparse(url).hostname or "").removeprefix("www.")
        return (
            self._host_map.get(host)
            or self._resolvers.get(name)
            or self._domain_map.get(name)
        )

    def resolver_name(self, url: str) -> str | None:
        """Name of the resolver the dispatch takes for *url* (step 1 of
        ``resolve()``): a host a resolver claims, then the second-level
        domain as a resolver's name or alias. ``None`` without one, where
        the dispatch follows redirects or probes the content type.
        """
        url = url.strip()
        resolver = self._resolver_for(url, extract_domain(url))
        return resolver.name if resolver is not None else None

    @property
    def supported_hosters(self) -> list[str]:
        """Return list of hosters with registered resolvers."""
        return list(self._resolvers.keys())

    def canonical_hoster(self, name: str) -> str | None:
        """Resolver name for a hoster label, second-level domain or host.

        Mirror domains and aliases map to their resolver (``filelions`` →
        ``vidhide``), so streams of one hoster share one name for
        deduplication and ranking; claimed hosts too (``kinoger.pw`` →
        ``strmup``). ``None`` when no resolver handles *name*.
        """
        resolver = (
            self._resolvers.get(name)
            or self._domain_map.get(name)
            or self._host_map.get(name)
        )
        return resolver.name if resolver is not None else None

    async def cleanup(self) -> None:
        """Close resources held by resolvers that have a cleanup method."""
        for resolver in self._resolvers.values():
            cleanup_fn = getattr(resolver, "cleanup", None)
            if cleanup_fn is not None:
                await cleanup_fn()

    def cached(self, url: str) -> tuple[bool, ResolvedStream | None]:
        """The cached outcome of ``resolve(url)``, without resolving.

        ``(True, stream)`` for a cached stream, ``(True, None)`` for a link
        cached as dead, ``(False, None)`` when ``resolve()`` would have to
        resolve it. A cached outcome a request takes is demand for its
        resolver (``demand_age()``).
        """
        cached = self._result_cache.get(url.strip())
        if cached is None or cached.is_expired:
            return False, None
        self._demand[cached.resolver] = time.monotonic()
        return True, cached.value

    def demand_age(self, resolver: str) -> float | None:
        """Seconds since a lookup last asked for a link of *resolver*:
        ``resolve()`` (its cache hits included) and ``cached()``, not a
        lookup without demand (Filemoon's pass keeper); ``None`` without
        one. It outlives a restart with the state (``export_state()``)."""
        last = self._demand.get(resolver)
        return None if last is None else time.monotonic() - last

    def last_stream(self, resolver: str) -> str | None:
        """The link of *resolver*'s newest cached stream (the one to expire
        last), as ``resolve()`` was asked for it; ``None`` without one: the
        link Filemoon's pass keeper resolves again (``PassKeeper``)."""
        now = time.monotonic()
        newest = max(
            (
                (entry.expires_at, url)
                for url, entry in self._result_cache.items()
                if entry.resolver == resolver
                and entry.value is not None
                and entry.expires_at > now
            ),
            default=None,
        )
        return None if newest is None else newest[1]

    @property
    def changes(self) -> int:
        """How often a resolution or a redirect was cached: what
        :meth:`export_state` holds changed (the state store writes then)."""
        return self._changes

    def export_state(self) -> dict[str, Any]:
        """The unexpired resolutions and redirects with their lifetime left
        (``remaining``, seconds), oldest first, and each resolver's demand
        (``demand``: seconds since its last lookup), in builtins only: a
        snapshot that outlives the process (``HosterStateStore``)."""
        now = time.monotonic()
        results = [
            {
                "url": url,
                "stream": None if entry.value is None else _plain(entry.value),
                "remaining": entry.expires_at - now,
                "resolver": entry.resolver,
            }
            for url, entry in self._result_cache.items()
            if entry.expires_at > now
        ]
        redirects = [
            {"url": url, "target": entry.value, "remaining": entry.expires_at - now}
            for url, entry in self._redirect_cache.items()
            if entry.expires_at > now
        ]
        demand = {name: now - last for name, last in self._demand.items()}
        return {"results": results, "redirects": redirects, "demand": demand}

    def import_state(self, state: Mapping[str, Any], age_s: float) -> tuple[int, int]:
        """Restore :meth:`export_state`'s entries with their lifetime
        shortened by *age_s* (the time since the export); the ones that ran
        out meanwhile stay out. The demand grows older by *age_s* (a
        snapshot without it has none). Returns the numbers of resolutions
        and redirects restored.

        Raises ``AttributeError``, ``KeyError``, ``TypeError`` or
        ``ValueError`` for a malformed entry, before changing anything.
        """
        results: dict[str, _CacheEntry[ResolvedStream | None]] = {}
        for entry in state["results"]:
            remaining = float(entry["remaining"]) - age_s
            stream = entry["stream"]
            value = None if stream is None else _from_plain(stream)
            if remaining > 0:
                results[str(entry["url"])] = _CacheEntry(
                    value, remaining, str(entry["resolver"])
                )
        redirects: dict[str, _CacheEntry[str]] = {}
        for entry in state["redirects"]:
            remaining = float(entry["remaining"]) - age_s
            if remaining > 0:
                redirects[str(entry["url"])] = _CacheEntry(
                    str(entry["target"]), remaining
                )
        now = time.monotonic()
        demand = {
            str(name): now - float(ago) - age_s
            for name, ago in (state.get("demand") or {}).items()
        }
        for name, last in demand.items():
            self._demand[name] = max(last, self._demand.get(name, last))
        self._result_cache.update(results)
        self._enforce_max_size(self._result_cache)
        self._redirect_cache.update(redirects)
        self._enforce_max_size(self._redirect_cache)
        return len(results), len(redirects)

    async def resolve(
        self,
        url: str,
        hoster: str = "",
        *,
        refresh: bool = False,
        demand: bool = True,
        bound: float | None = None,
    ) -> ResolvedStream | None:
        """Resolve a hoster embed URL to a playable video URL. The lookup is
        demand for the resolver it goes to (``demand_age()``), a cache hit
        too, unless *demand* is false (Filemoon's pass keeper: the app's own
        lookup). *bound* replaces the resolve bound for this lookup (the
        keeper's again: no request waits for its capture).

        1. Check result cache for previously resolved URL (not with
           *refresh*: the CDN refused the cached stream, and a refresh
           passes the challenge cooldown too); probe a streaming playlist
           URL (``.m3u8``, ``.mpd``) directly.
        2. Try the specific hoster resolver (URL domain takes priority over hint).
        3. If URL domain has no resolver, follow HTTP redirects and retry.
        4. Try hoster hint if different from URL domain (handles redirect domains).
        5. Fall back to content-type probing (HEAD request).
        6. Cache the result (alive or dead) and return; a timeout or network
           error is not cached (it says nothing about the link).
        """
        # Scraped links sometimes carry surrounding whitespace (trailing "\n")
        url = url.strip()

        # Periodic eviction of expired cache entries
        self._resolve_count += 1
        if self._resolve_count % _EVICT_INTERVAL == 0:
            self._evict_expired()

        # 0. Check result cache
        cached = None if refresh else self._result_cache.get(url)
        if cached is not None and not cached.is_expired:
            log.debug("hoster_resolve_cache_hit", url=url)
            self._telemetry.count("hoster_resolve", "cached", resolver=cached.resolver)
            if demand:
                self._demand[cached.resolver] = time.monotonic()
            return cached.value

        # URL domain is authoritative; fall back to plugin-provided hint
        hoster_name = extract_domain(url) or hoster

        # A streaming playlist needs no hoster resolver: moflix hands out its
        # own HLS playlists on moflix-stream.day, and that domain's resolver
        # (VidHide) expects an embed page and failed on every one
        if _is_playlist_url(url):
            return await self._probe(url, hoster_name)

        # 1. Try specific resolver for URL host or domain (claimed host, name
        #    match, then domain alias)
        resolver = self._resolver_for(url, hoster_name)
        if resolver is not None:
            return await self._resolve_with(
                resolver,
                hoster_name,
                url,
                url,
                refresh=refresh,
                demand=demand,
                bound=bound,
            )

        # 2. No resolver for this domain — try following redirects
        final_url = await self._follow_redirects(url)
        if final_url:
            redirected_hoster = extract_domain(final_url)
            resolver = self._resolver_for(final_url, redirected_hoster)
            if resolver is not None:
                log.info(
                    "hoster_resolve_after_redirect",
                    original=hoster_name,
                    redirected=redirected_hoster,
                    url=final_url,
                )
                return await self._resolve_with(
                    resolver,
                    redirected_hoster,
                    final_url,
                    url,
                    refresh=refresh,
                    demand=demand,
                    bound=bound,
                )

        # 3. Try hoster hint if different from URL domain
        #    Handles rotating redirect domains (e.g., lauradaydo.com for VOE)
        if hoster and hoster != hoster_name:
            resolver = self._resolvers.get(hoster) or self._domain_map.get(hoster)
            if resolver is not None:
                log.info(
                    "hoster_resolve_via_hint",
                    hint=hoster,
                    url_domain=hoster_name,
                    url=url,
                )
                return await self._resolve_with(
                    resolver,
                    hoster,
                    url,
                    url,
                    refresh=refresh,
                    demand=demand,
                    bound=bound,
                )

        # 4. Fallback: content-type probing
        self._count_unresolved(hoster_name)
        return await self._probe(url, hoster_name)

    def _count_unresolved(self, hoster: str) -> None:
        """A link no resolver claims: which resolver to build next."""
        log.info("hoster_without_resolver", hoster=hoster)
        # Scraped links name the hosters, so the set is open: new ones stop
        # counting at the cap, the counted ones go on
        if hoster in self._unresolved or len(self._unresolved) < _MAX_UNRESOLVED:
            self._unresolved[hoster] += 1

    def unresolved_hosts(self, top: int = 20) -> dict[str, int]:
        """The hosters probed for want of a resolver, the most frequent first."""
        return dict(self._unresolved.most_common(top))

    async def _probe(self, url: str, hoster_name: str) -> ResolvedStream | None:
        """Content-type probe of a URL without a resolver (``direct``), cached."""
        with self._telemetry.stage("hoster_resolve", resolver=_DIRECT) as stage:
            result = await self._probe_content_type(url, hoster_name)
            stage.outcome = "dead" if result is None else "stream"
        if result is not None:
            result = _stamped(result)
        self._cache_result(url, result, _DIRECT)
        return result

    def _cache_result(
        self, url: str, result: ResolvedStream | None, resolver: str
    ) -> None:
        """Cache a resolution result with appropriate TTL.

        When the cache exceeds ``_MAX_CACHE_SIZE``, the oldest entries
        (by insertion order) are evicted to make room.
        """
        ttl = _CACHE_TTL_ALIVE if result is not None else _CACHE_TTL_DEAD
        self._result_cache[url] = _CacheEntry(result, ttl, resolver)
        self._enforce_max_size(self._result_cache)
        self._changes += 1

    def _evict_expired(self) -> None:
        """Remove expired entries from result and redirect caches."""
        for cache in (self._result_cache, self._redirect_cache):
            expired = [k for k, v in cache.items() if v.is_expired]
            for k in expired:
                del cache[k]
        log.debug(
            "hoster_cache_evict",
            result_cache_size=len(self._result_cache),
            redirect_cache_size=len(self._redirect_cache),
        )

    @staticmethod
    def _enforce_max_size[T](cache: dict[str, _CacheEntry[T]]) -> None:
        """Evict oldest entries when cache exceeds ``_MAX_CACHE_SIZE``."""
        if len(cache) <= _MAX_CACHE_SIZE:
            return
        # Python dicts preserve insertion order; pop from the front
        excess = len(cache) - _MAX_CACHE_SIZE
        keys = list(cache.keys())[:excess]
        for k in keys:
            del cache[k]

    async def _judge(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        result: ResolvedStream | None,
        *,
        started: float,
        challenge: ResolutionChallenge | None = None,
    ) -> tuple[str, ResolvedStream | None, bool]:
        """Classify a resolver's answer: ``dead`` link, ``unplayable``,
        ``check_error`` or a ``stream``; the stream only for the last, and
        whether the outcome may be cached. The success log names the
        *challenge* the capture passed on its way (a cold capture), ``None``
        for a warm one, and the attempt's wall time since *started*
        (``time.monotonic()``) as ``duration_ms``.

        A failed playback check (timeout, reset) says nothing about the
        stream, like a failed resolver request: neither cached nor counted.
        A resolver without a check of its own (``needs_playback_check``) is
        checked with *verify_playback* off too. A resolver whose CDN binds
        the URL to the resolving address (``address_bound``) marks its
        stream, so the player fetches it through Scavengarr.
        """
        if result is None:
            log.warning("hoster_resolve_failed", hoster=hoster_name, url=url)
            return "dead", None, True
        unchecked = getattr(resolver, "needs_playback_check", False) is True
        if (self._verify_playback or unchecked) and self._http_client is not None:
            try:
                check = await check_playable(self._http_client, result)
            except httpx.HTTPError as exc:
                log.info(
                    "hoster_resolve_check_error",
                    hoster=hoster_name,
                    error=type(exc).__name__,
                )
                return "check_error", None, False
            if not check.playable:
                log.warning("hoster_resolve_unplayable", hoster=hoster_name, url=url)
                self._record(resolver, failed=True)
                return "unplayable", None, True
            result = _measured(result, check)
        log.info(
            "hoster_resolve_success",
            hoster=hoster_name,
            is_hls=result.is_hls,
            challenge=challenge,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        self._record(resolver, failed=False)
        if getattr(resolver, "address_bound", False) is True:
            result = replace(result, address_bound=True)
        return "stream", _stamped(result), True

    def _record(self, resolver: HosterResolverPort, *, failed: bool) -> None:
        """Report a resolution's outcome to the circuit breaker; a stream
        also ends the resolver's challenge cooldown."""
        if not failed:
            self._challenges.streamed(resolver.name)
        breaker = self._circuit_breaker
        if breaker is None:
            return
        if failed:
            breaker.record_failure(resolver.name)
        else:
            breaker.record_success(resolver.name)

    def bound_headers(self, url: str, hoster: str = "") -> tuple[str, ...]:
        """Request headers the CDN of *url*'s hoster binds video URLs to.

        Empty for most hosters: their video URLs play for any player.
        """
        url = url.strip()
        resolver = self._resolver_for(url, extract_domain(url) or hoster)
        if isinstance(resolver, ClientBoundResolverPort):
            return resolver.bound_headers
        return ()

    async def resolve_for_client(
        self, url: str, hoster: str, headers: Mapping[str, str]
    ) -> ResolvedStream | None:
        """Resolve *url* for the player sending *headers* (lower-case names).

        Only for a hoster whose CDN binds video URLs to the player's headers
        (``bound_headers``), else ``None``. Not cached: the stream belongs to
        that player, and the caller keeps it per player. The circuit breaker
        and the resolve timeout apply as in ``resolve``.
        """
        url = url.strip()
        hoster_name = extract_domain(url) or hoster
        resolver = self._resolver_for(url, hoster_name)
        if not isinstance(resolver, ClientBoundResolverPort):
            return None

        async def attempt() -> ResolvedStream | None:
            stream, _ = await self._try_resolver(
                resolver,
                hoster_name,
                url,
                resolve=partial(resolver.resolve_for_client, url, headers),
            )
            return stream

        return await self._guarded(resolver, url, attempt)

    async def _resolve_with(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str,
        *,
        refresh: bool = False,
        demand: bool = True,
        bound: float | None = None,
    ) -> ResolvedStream | None:
        """Resolve *url* with *resolver*; cache the outcome under *cache_key*.
        A link whose capture runs on in the background joins it instead
        (``_joined``). With *demand* the lookup is the resolver's demand;
        *bound* replaces the resolve bound."""
        if demand:
            self._demand[resolver.name] = time.monotonic()
        if bound is None:
            bound = self._resolve_timeout
        completion = self._completing.get(cache_key)
        if completion is not None:
            return await self._joined(completion, resolver, url, bound)
        return await self._guarded(
            resolver,
            url,
            partial(self._attempt, resolver, hoster_name, url, cache_key, bound),
            refresh=refresh,
        )

    async def _joined(
        self,
        completion: asyncio.Task[ResolvedStream | None],
        resolver: HosterResolverPort,
        url: str,
        bound: float,
    ) -> ResolvedStream | None:
        """Wait for the background *completion* of *url*'s capture, at most
        the lookup's *bound*, and answer the stream it cached; ``None``
        without one in time. A second capture of the link would end without
        the first's challenge and cache the link as dead over the
        completion's stream (code review 2026-10-12, finding 9). The wait
        leaves the completion running when the bound or the request ends
        it."""
        log.info("hoster_resolve_joined", hoster=resolver.name, url=url)
        self._telemetry.count("hoster_resolve", "joined", resolver=resolver.name)
        await asyncio.wait((completion,), timeout=bound)
        if not completion.done() or completion.cancelled():
            return None
        if completion.exception() is not None:
            return None
        return completion.result()

    async def _guarded(
        self,
        resolver: HosterResolverPort,
        url: str,
        attempt: Callable[[], Coroutine[Any, Any, ResolvedStream | None]],
        *,
        refresh: bool = False,
    ) -> ResolvedStream | None:
        """Run *attempt* unless the challenge cooldown or the circuit breaker
        skips *resolver*.

        The cooldown goes first: the breaker's ``allow()`` starts a half-open
        trial, and a link the cooldown skips must not take the trial's slot.
        A *refresh* (the CDN refused a stored stream at play time) passes the
        cooldown, which spares captures for links nobody plays yet; only an
        open breaker refuses it, as before the cooldown (code review
        2026-10-12, finding 5: playback failed at once in a cooldown).
        A half-open probe runs to its end even when the request is cut: cut
        by the resolve grace, Filemoon's probes never reported, so the
        breaker probed again after every cooldown without doubling it
        (production, 2026-10-05). A probe that finds a stream closes the
        breaker and leaves the stream in the cache for the next request; a
        probe without a verdict (a dead link, a failed request) frees the
        probe slot, so the next link probes (it held the slot for a whole
        cooldown, code review 2026-10-06).
        """
        if not refresh and (left := self._challenges.left(resolver.name)):
            log.info(
                "hoster_resolve_challenge_cooldown",
                hoster=resolver.name,
                url=url,
                left_s=round(left),
            )
            self._telemetry.count(
                "hoster_resolve", "challenge_cooldown", resolver=resolver.name
            )
            return None
        breaker = self._circuit_breaker
        if breaker is None:
            return await attempt()
        if not breaker.allow(resolver.name):
            log.info("hoster_resolve_circuit_open", hoster=resolver.name, url=url)
            self._telemetry.count(
                "hoster_resolve", "breaker_open", resolver=resolver.name
            )
            return None
        if breaker.state(resolver.name) != "half_open":
            return await attempt()
        probe = asyncio.ensure_future(attempt())
        self._probes.add(probe)
        probe.add_done_callback(self._probes.discard)
        probe.add_done_callback(lambda _: breaker.release(resolver.name))
        return await asyncio.shield(probe)

    async def _attempt(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str,
        bound: float,
    ) -> ResolvedStream | None:
        """One resolution of *url* within *bound*; its outcome is cached
        under *cache_key*."""
        result, cacheable = await self._try_resolver(
            resolver, hoster_name, url, cache_key=cache_key, bound=bound
        )
        if cacheable:
            self._cache_result(cache_key, result, resolver.name)
        return result

    async def aclose(self) -> None:
        """Cancel the half-open probes and the challenged captures that run
        on past the bound, with their completions (at shutdown)."""
        running: list[asyncio.Task[Any]] = [*self._probes]
        for completion, work in self._completions.values():
            running += (completion, work)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    async def _try_resolver(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        *,
        resolve: Callable[[], Awaitable[ResolvedStream | None]] | None = None,
        cache_key: str | None = None,
        bound: float | None = None,
    ) -> tuple[ResolvedStream | None, bool]:
        """Attempt resolution with a specific resolver, logging success/failure.

        Returns the stream (None = failed) and whether the outcome may be
        cached. The resolver gets ``resolve_timeout`` in total, or the
        lookup's own *bound* (the resolvers' own request timeouts add up
        over several requests); the clock stops while it waits for a
        stealth browser page (``work_clock``). *resolve* replaces
        ``resolver.resolve(url)`` (a player's resolution).

        The circuit breaker counts a timeout and an unplayable stream; a
        stream resets it. A dead link, a failed request and a cut neither
        count nor reset it: they say nothing about the hoster. Most cuts
        come from the answer going out once enough other hosters have a
        video, and five of them opened the breakers of healthy hosters
        (code review, 2026-10-06). Neither does a capture that got no
        browser page before its request was due (``busy``).

        A resolution without a stream whose challenge still stands
        (``challenge_mark``: an interstitial, a Turnstile widget, a captcha
        provider's frame, the hoster's own captcha, not got past since) ends
        as ``challenge``: the hoster wants a warm browser context, it is not
        broken. Neither cached nor counted; the resolver's links wait out
        the challenge cooldown. A page past its challenge gave a verdict on
        the link: no stream there is a dead link (code review 2026-10-12,
        finding 2). A timeout after any challenge ends as ``challenge`` too
        (the cold path cost the bound), its cooldown only while the
        challenge stands. A capture the bound cuts after its challenge runs
        on in the background when its outcome is cached under *cache_key*
        (``_hand_over``); a cut request (the answer went out) cancels it, as
        it cancels a capture without a challenge.
        """
        mark = ChallengeMark()
        started = time.monotonic()
        cancels = _cancels()
        with self._telemetry.stage("hoster_resolve", resolver=resolver.name) as stage:
            try:
                async with asyncio.timeout(
                    self._resolve_timeout if bound is None else bound
                ) as clock:
                    work = _started(
                        resolve or partial(resolver.resolve, url), clock, mark
                    )
                    try:
                        result = await asyncio.shield(work)
                    except asyncio.CancelledError:
                        # Cut by the bound (its clock expired, its cancel the
                        # only new one) or the request, also when the request
                        # cancels in the bound's loop iteration (code review
                        # 2026-10-12, finding 15). The work ends inside the
                        # bound: the page gate gives a stopped clock its time
                        # back, an exited one refuses
                        if not (
                            clock.expired()
                            and _cancels() == cancels + 1
                            and self._hand_over(
                                work,
                                resolver,
                                hoster_name,
                                url,
                                cache_key,
                                mark,
                                started,
                            )
                        ):
                            await _cancelled(work)
                        raise
                if result is None and mark.open:
                    stage.outcome = self._challenged(resolver, hoster_name, url, mark)
                    return None, False
                stage.outcome, stream, cacheable = await self._judge(
                    resolver,
                    hoster_name,
                    url,
                    result,
                    started=started,
                    challenge=mark.kind,
                )
                return stream, cacheable
            except PageBusy:
                log.info("hoster_resolve_busy", hoster=hoster_name, url=url)
                stage.outcome = "busy"
                return None, False
            except (TimeoutError, httpx.TimeoutException):
                if mark.kind is not None:
                    stage.outcome = self._challenged(resolver, hoster_name, url, mark)
                    return None, False
                log.warning("hoster_resolve_timeout", hoster=hoster_name, url=url)
                self._record(resolver, failed=True)
                stage.outcome = "timeout"
                return None, False
            except httpx.TransportError as exc:
                log.warning(
                    "hoster_resolve_network_error",
                    hoster=hoster_name,
                    url=url,
                    error=str(exc),
                )
                stage.outcome = "network_error"
                return None, False
            except httpx.HTTPError as exc:
                log.warning(
                    "hoster_resolve_http_error",
                    hoster=hoster_name,
                    url=url,
                    error=str(exc),
                )
                stage.outcome = "http_error"
            except Exception:
                log.exception("hoster_resolve_error", hoster=hoster_name, url=url)
                stage.outcome = "error"
        return None, True

    def _challenged(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        mark: ChallengeMark,
    ) -> str:
        """A resolution that met a challenge and found no stream: start the
        resolver's next cooldown step while the challenge stands (a cut past
        it starts none: the context is warm); returns the outcome."""
        cooldown = self._challenges.challenged(resolver.name) if mark.open else 0.0
        log.info(
            "hoster_resolve_challenge",
            hoster=hoster_name,
            url=url,
            kind=mark.kind,
            passed=not mark.open,
            cooldown_s=round(cooldown),
        )
        return "challenge"

    def _hand_over(
        self,
        work: asyncio.Task[ResolvedStream | None],
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str | None,
        mark: ChallengeMark,
        started: float,
    ) -> bool:
        """Let *work*, a capture the bound cut, run on in the background
        (``_complete``) when it met a challenge: only a resolution whose
        outcome is cached (*cache_key*; a player's own is not), one per
        resolver at a time; requests for the link join it (``_joined``).
        Each holds a browser page, so they leave one of the gate's pages
        free: a /play resolution waited for one until it gave up (code
        review 2026-10-12, finding 12), and on a single page nothing runs
        on. Whether it runs on."""
        name = resolver.name
        if mark.kind is None or cache_key is None or name in self._completions:
            return False
        if self._pages is not None and len(self._completions) >= self._pages.limit - 1:
            return False
        completion = asyncio.ensure_future(
            self._complete(work, resolver, hoster_name, url, cache_key, mark, started)
        )
        self._completions[name] = (completion, work)
        self._completing[cache_key] = completion
        # A completion cancelled before its first step never awaited the
        # capture, which would run on unowned
        completion.add_done_callback(lambda _: work.cancel())
        completion.add_done_callback(lambda _: self._completions.pop(name, None))
        completion.add_done_callback(lambda _: self._completing.pop(cache_key, None))
        return True

    async def _complete(
        self,
        work: asyncio.Task[ResolvedStream | None],
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str,
        mark: ChallengeMark,
        started: float,
    ) -> ResolvedStream | None:
        """Await a capture the bound cut after a challenge for up to
        ``_COMPLETION_S`` more, on the page it holds (it takes no other);
        returns the stream it cached, for the requests that joined it.

        Its stream is judged and cached under *cache_key* as the attempt's
        own: it ends the resolver's challenge cooldown, closes a half-open
        breaker, and the next request finds it in the cache. No stream from
        a page past its challenge (*mark*) is a dead link, judged and cached
        as one. Without a stream while the challenge stands (none, an error,
        the deadline, which cancels the capture) it stands as in
        ``_try_resolver``: nothing is cached or counted, and the resolver's
        next cooldown step starts now; past it, an error or the deadline
        gives no verdict and steps nothing up. Recorded as the stage
        ``hoster_challenge_completion`` from the cut; shutdown cancels it
        (``aclose``).
        """
        deadline = asyncio.timeout(_COMPLETION_S)
        with self._telemetry.stage(
            "hoster_challenge_completion", resolver=resolver.name
        ) as stage:
            # Unless it ends otherwise: cut by the deadline or by shutdown
            stage.outcome = "cancelled"
            try:
                async with deadline:
                    result = await work
            except Exception:
                if not deadline.expired():
                    stage.outcome = "none"
                    log.exception(
                        "hoster_challenge_completion_error", hoster=hoster_name, url=url
                    )
                if mark.open:
                    self._unsolved(resolver, hoster_name, url)
                return None
            if result is None and mark.open:
                stage.outcome = "none"
                self._unsolved(resolver, hoster_name, url)
                return None
            try:
                _, stream, cacheable = await self._judge(
                    resolver,
                    hoster_name,
                    url,
                    result,
                    started=started,
                    challenge=mark.kind,
                )
            except Exception:
                # The capture got its stream, the check broke: no verdict on
                # the link or its challenge (code review 2026-10-12, V29)
                stage.outcome = "none"
                log.exception(
                    "hoster_challenge_completion_error", hoster=hoster_name, url=url
                )
                return None
            if not cacheable:
                stage.outcome = "none"
                return None
            self._cache_result(cache_key, stream, resolver.name)
            stage.outcome = "none" if stream is None else "stream"
            return stream

    def _unsolved(
        self, resolver: HosterResolverPort, hoster_name: str, url: str
    ) -> None:
        """A completion without a stream: the resolver's challenge stands,
        and its next cooldown step starts now."""
        cooldown = self._challenges.unsolved(resolver.name)
        log.info(
            "hoster_challenge_unsolved",
            hoster=hoster_name,
            url=url,
            cooldown_s=round(cooldown),
        )

    async def _follow_redirects(self, url: str) -> str | None:
        """Follow HTTP redirects and return final URL if domain changed.

        Uses a redirect cache to avoid repeated lookups for rotating
        mirror domains. Cache TTL: 1 hour.
        """
        # Check redirect cache
        cached = self._redirect_cache.get(url)
        if cached is not None and not cached.is_expired:
            return cached.value  # type: ignore[return-value]

        if self._http_client is None:
            return None
        try:
            resp = await self._http_client.head(
                url, follow_redirects=True, timeout=self._resolve_timeout
            )
            final_url = str(resp.url)
            if final_url != url:
                log.debug(
                    "hoster_redirect_followed",
                    original=url,
                    final=final_url,
                )
                self._redirect_cache[url] = _CacheEntry(final_url, _CACHE_TTL_REDIRECT)
                self._enforce_max_size(self._redirect_cache)
                self._changes += 1
                return final_url
        except httpx.TimeoutException:
            log.debug("hoster_redirect_timeout", url=url)
        except httpx.HTTPError as exc:
            log.debug("hoster_redirect_http_error", url=url, error=str(exc))
        except Exception:  # noqa: BLE001
            log.debug("hoster_redirect_follow_failed", url=url)
        return None

    async def _probe_content_type(
        self,
        url: str,
        hoster_name: str,
    ) -> ResolvedStream | None:
        """Probe URL via HEAD request to check if it's directly playable.

        Returns a ResolvedStream if the URL answers (below 400) with a video
        type (video/*, an HLS type of ``_HLS_CONTENT_TYPES``): a CDN that types its
        error page by the path made a refusal a stream, cached for an hour
        (code review, 2026-10-06).
        """
        if self._http_client is None:
            return None

        try:
            resp = await self._http_client.head(
                url, follow_redirects=True, timeout=self._resolve_timeout
            )
            if resp.status_code >= 400:
                log.debug(
                    "hoster_probe_refused", hoster=hoster_name, status=resp.status_code
                )
                return None
            content_type = resp.headers.get("content-type", "").lower()

            if content_type.startswith("video/"):
                log.info(
                    "hoster_probe_direct_video",
                    hoster=hoster_name,
                    content_type=content_type,
                )
                return ResolvedStream(
                    video_url=str(resp.url), quality=StreamQuality.UNKNOWN
                )

            if any(hls in content_type for hls in _HLS_CONTENT_TYPES):
                log.info(
                    "hoster_probe_hls",
                    hoster=hoster_name,
                    content_type=content_type,
                )
                return ResolvedStream(
                    video_url=str(resp.url),
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )

        except httpx.TimeoutException:
            log.debug("hoster_probe_timeout", hoster=hoster_name, url=url)
        except httpx.HTTPError as exc:
            log.debug(
                "hoster_probe_http_error",
                hoster=hoster_name,
                url=url,
                error=str(exc),
            )
        except Exception:  # noqa: BLE001
            log.debug("hoster_probe_failed", hoster=hoster_name, url=url)

        return None
