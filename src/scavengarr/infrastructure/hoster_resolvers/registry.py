"""Registry that dispatches hoster URL resolution to per-hoster resolvers."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.domain.ports.hoster_resolver import HosterResolverPort
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers._verify import check_playable

log = structlog.get_logger(__name__)

# Cache TTLs for resolver results
_CACHE_TTL_ALIVE = 3600  # 1 hour for successful resolutions
_CACHE_TTL_DEAD = 900  # 15 minutes for failed resolutions
_CACHE_TTL_REDIRECT = 3600  # 1 hour for redirect mappings

# Evict expired entries every N resolve() calls
_EVICT_INTERVAL = 1000

# Maximum number of entries in each cache (result + redirect)
_MAX_CACHE_SIZE = 10_000

# Streaming playlists, which plugins sometimes hand out directly. Not file
# suffixes: hoster pages end in the file name (streamtape /v/<id>/x.mp4)
_PLAYLIST_SUFFIXES = (".m3u8", ".mpd")


def _is_playlist_url(url: str) -> bool:
    return urlparse(url).path.lower().endswith(_PLAYLIST_SUFFIXES)


def extract_domain(url: str) -> str:
    """Extract the second-level domain from a URL.

    Returns the second-to-last segment of the hostname (e.g.
    ``"voe"`` from ``"https://voe.sx/e/abc"``).  Handles ``www.``
    prefixes automatically since ``parts[-2]`` skips them.

    Returns ``""`` when the URL cannot be parsed or has fewer than
    two hostname segments.
    """
    try:
        hostname = urlparse(url).hostname or ""
        parts = hostname.split(".")
        return parts[-2] if len(parts) >= 2 else ""
    except Exception:  # noqa: BLE001
        return ""


class _CacheEntry[T]:
    """Time-bounded cache entry (resolver results, redirect targets)."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: T, ttl: int) -> None:
        self.value = value
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
    resolver name, so a hoster's mirror domains share it).
    """

    def __init__(
        self,
        resolvers: list[HosterResolverPort] | None = None,
        http_client: httpx.AsyncClient | None = None,
        resolve_timeout: float = 15.0,
        verify_playback: bool = False,
        circuit_breaker: PluginCircuitBreaker | None = None,
    ) -> None:
        self._resolvers: dict[str, HosterResolverPort] = {}
        self._domain_map: dict[str, HosterResolverPort] = {}
        self._host_map: dict[str, HosterResolverPort] = {}
        self._http_client = http_client
        self._resolve_timeout = resolve_timeout
        self._circuit_breaker = circuit_breaker
        # Resolver results must also pass check_playable (needs http_client)
        self._verify_playback = verify_playback and http_client is not None
        self._result_cache: dict[str, _CacheEntry[ResolvedStream | None]] = {}
        self._redirect_cache: dict[str, _CacheEntry[str]] = {}
        self._resolve_count = 0
        # Half-open probes run on when their request is cut (see _resolve_with)
        self._probes: set[asyncio.Task[ResolvedStream | None]] = set()
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

    @property
    def supported_hosters(self) -> list[str]:
        """Return list of hosters with registered resolvers."""
        return list(self._resolvers.keys())

    @property
    def supported_domains(self) -> frozenset[str]:
        """Return second-level domains that dispatch to a resolver.

        Resolver names plus every alias from ``supported_domains``
        (e.g. ``filelions`` for the vidhide resolver).
        """
        return frozenset(self._resolvers) | frozenset(self._domain_map)

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
        resolve it.
        """
        cached = self._result_cache.get(url.strip())
        if cached is None or cached.is_expired:
            return False, None
        return True, cached.value

    async def resolve(
        self, url: str, hoster: str = "", *, refresh: bool = False
    ) -> ResolvedStream | None:
        """Resolve a hoster embed URL to a playable video URL.

        1. Check result cache for previously resolved URL (not with
           *refresh*: the CDN refused the cached stream); probe a streaming
           playlist URL (``.m3u8``, ``.mpd``) directly.
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
            return cached.value

        # URL domain is authoritative; fall back to plugin-provided hint
        hoster_name = extract_domain(url) or hoster

        # A streaming playlist needs no hoster resolver: moflix hands out its
        # own HLS playlists on moflix-stream.day, and that domain's resolver
        # (VidHide) expects an embed page and failed on every one
        if _is_playlist_url(url):
            result = await self._probe_content_type(url, hoster_name)
            self._cache_result(url, result)
            return result

        # 1. Try specific resolver for URL host or domain (claimed host, name
        #    match, then domain alias)
        resolver = self._resolver_for(url, hoster_name)
        if resolver is not None:
            return await self._resolve_with(resolver, hoster_name, url, url)

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
                    resolver, redirected_hoster, final_url, url
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
                return await self._resolve_with(resolver, hoster, url, url)

        # 4. Fallback: content-type probing
        result = await self._probe_content_type(url, hoster_name)
        self._cache_result(url, result)
        return result

    def _cache_result(self, url: str, result: ResolvedStream | None) -> None:
        """Cache a resolution result with appropriate TTL.

        When the cache exceeds ``_MAX_CACHE_SIZE``, the oldest entries
        (by insertion order) are evicted to make room.
        """
        ttl = _CACHE_TTL_ALIVE if result is not None else _CACHE_TTL_DEAD
        self._result_cache[url] = _CacheEntry(result, ttl)
        self._enforce_max_size(self._result_cache)

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
    ) -> tuple[ResolvedStream | None, bool]:
        """Classify a resolver's answer: dead link, unplayable or a stream."""
        if result is None:
            log.warning("hoster_resolve_failed", hoster=hoster_name, url=url)
            return None, True
        if (
            self._verify_playback
            and self._http_client is not None
            and not await check_playable(self._http_client, result)
        ):
            log.warning("hoster_resolve_unplayable", hoster=hoster_name, url=url)
            self._record(resolver, failed=True)
            return None, True
        log.info("hoster_resolve_success", hoster=hoster_name, is_hls=result.is_hls)
        self._record(resolver, failed=False)
        return result, True

    def _record(self, resolver: HosterResolverPort, *, failed: bool) -> None:
        """Report a resolution's outcome to the circuit breaker."""
        breaker = self._circuit_breaker
        if breaker is None:
            return
        if failed:
            breaker.record_failure(resolver.name)
        else:
            breaker.record_success(resolver.name)

    async def _resolve_with(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str,
    ) -> ResolvedStream | None:
        """Resolve *url* with *resolver*; cache the outcome under *cache_key*.

        While the circuit breaker is open the resolver is skipped. Its
        half-open probe runs to its end even when the request is cut: cut by
        the resolve grace, Filemoon's probes never reported, so the breaker
        probed again after every cooldown without doubling it (production,
        2026-10-05). A probe that finds a stream closes the breaker and
        leaves the stream in the cache for the next request.
        """
        breaker = self._circuit_breaker
        if breaker is not None and not breaker.allow(resolver.name):
            log.info("hoster_resolve_circuit_open", hoster=resolver.name, url=url)
            return None
        attempt = self._attempt(resolver, hoster_name, url, cache_key)
        if breaker is None or breaker.state(resolver.name) != "half_open":
            return await attempt
        probe = asyncio.ensure_future(attempt)
        self._probes.add(probe)
        probe.add_done_callback(self._probes.discard)
        return await asyncio.shield(probe)

    async def _attempt(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str,
    ) -> ResolvedStream | None:
        """One resolution of *url*; its outcome is cached under *cache_key*."""
        result, cacheable = await self._try_resolver(resolver, hoster_name, url)
        if cacheable:
            self._cache_result(cache_key, result)
        return result

    async def aclose(self) -> None:
        """Cancel the half-open probes still running (at shutdown)."""
        probes = list(self._probes)
        for probe in probes:
            probe.cancel()
        await asyncio.gather(*probes, return_exceptions=True)

    async def _try_resolver(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
    ) -> tuple[ResolvedStream | None, bool]:
        """Attempt resolution with a specific resolver, logging success/failure.

        Returns the stream (None = failed) and whether the outcome may be
        cached. The resolver gets ``resolve_timeout`` in total (the
        resolvers' own request timeouts add up over several requests).

        The circuit breaker counts a timeout, a cut after half the timeout
        (the Stremio deadline ends most resolutions before the timeout) and
        an unplayable stream; a stream resets it. A dead link neither
        counts nor resets it: it says nothing about the hoster.
        """
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._resolve_timeout):
                result = await resolver.resolve(url)
            return await self._judge(resolver, hoster_name, url, result)
        except (TimeoutError, httpx.TimeoutException):
            log.warning("hoster_resolve_timeout", hoster=hoster_name, url=url)
            self._record(resolver, failed=True)
            return None, False
        except asyncio.CancelledError:
            if time.monotonic() - started >= self._resolve_timeout / 2:
                self._record(resolver, failed=True)
            raise
        except httpx.TransportError as exc:
            log.warning(
                "hoster_resolve_network_error",
                hoster=hoster_name,
                url=url,
                error=str(exc),
            )
            return None, False
        except httpx.HTTPError as exc:
            log.warning(
                "hoster_resolve_http_error",
                hoster=hoster_name,
                url=url,
                error=str(exc),
            )
        except Exception:
            log.exception("hoster_resolve_error", hoster=hoster_name, url=url)
        return None, True

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

        Returns a ResolvedStream if the URL points directly to a video file
        (video/*, application/vnd.apple.mpegurl, application/dash+xml).
        """
        if self._http_client is None:
            return None

        try:
            resp = await self._http_client.head(
                url, follow_redirects=True, timeout=self._resolve_timeout
            )
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

            if "application/vnd.apple.mpegurl" in content_type:
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
