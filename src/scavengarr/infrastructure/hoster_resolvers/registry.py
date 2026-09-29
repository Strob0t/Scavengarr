"""Registry that dispatches hoster URL resolution to per-hoster resolvers."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.domain.ports.hoster_resolver import HosterResolverPort
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


class _CacheEntry:
    """Time-bounded cache entry for resolver results."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: ResolvedStream | None, ttl: int) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class HosterResolverRegistry:
    """Dispatches hoster URL resolution to the appropriate resolver.

    Falls back to content-type probing when no specific resolver is registered.
    Caches resolution outcomes and redirect mappings in-memory.
    """

    def __init__(
        self,
        resolvers: list[HosterResolverPort] | None = None,
        http_client: httpx.AsyncClient | None = None,
        resolve_timeout: float = 15.0,
        verify_playback: bool = False,
    ) -> None:
        self._resolvers: dict[str, HosterResolverPort] = {}
        self._domain_map: dict[str, HosterResolverPort] = {}
        self._http_client = http_client
        self._resolve_timeout = resolve_timeout
        # Resolver results must also pass check_playable (needs http_client)
        self._verify_playback = verify_playback and http_client is not None
        self._result_cache: dict[str, _CacheEntry] = {}
        self._redirect_cache: dict[str, _CacheEntry] = {}
        self._resolve_count = 0
        for resolver in resolvers or []:
            self.register(resolver)

    def register(self, resolver: HosterResolverPort) -> None:
        """Register a resolver for a specific hoster.

        If the resolver exposes a ``supported_domains`` property, each
        domain is also mapped so that URL-based dispatch finds the
        resolver even when the URL domain differs from the resolver name
        (e.g. ``filelions`` → vidhide resolver). A domain claimed twice
        stays with the first resolver (the composition registers specific
        resolvers before the generic XFS/DDL ones) and is logged.
        """
        self._resolvers[resolver.name] = resolver
        domains: frozenset[str] | None = getattr(resolver, "supported_domains", None)
        for domain in domains or ():
            existing = self._domain_map.get(domain)
            if existing is not None and existing is not resolver:
                log.warning(
                    "hoster_domain_conflict",
                    domain=domain,
                    kept=existing.name,
                    ignored=resolver.name,
                )
                continue
            self._domain_map[domain] = resolver
        log.debug("hoster_resolver_registered", hoster=resolver.name)

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

    async def cleanup(self) -> None:
        """Close resources held by resolvers that have a cleanup method."""
        for resolver in self._resolvers.values():
            cleanup_fn = getattr(resolver, "cleanup", None)
            if cleanup_fn is not None:
                await cleanup_fn()

    async def resolve(self, url: str, hoster: str = "") -> ResolvedStream | None:
        """Resolve a hoster embed URL to a playable video URL.

        1. Check result cache for previously resolved URL.
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
        cached = self._result_cache.get(url)
        if cached is not None and not cached.is_expired:
            log.debug("hoster_resolve_cache_hit", url=url)
            return cached.value

        # URL domain is authoritative; fall back to plugin-provided hint
        hoster_name = extract_domain(url) or hoster

        # 1. Try specific resolver for URL domain (name match, then domain alias)
        resolver = self._resolvers.get(hoster_name) or self._domain_map.get(hoster_name)
        if resolver is not None:
            return await self._resolve_with(resolver, hoster_name, url, url)

        # 2. No resolver for this domain — try following redirects
        final_url = await self._follow_redirects(url)
        if final_url:
            redirected_hoster = extract_domain(final_url)
            resolver = self._resolvers.get(redirected_hoster) or self._domain_map.get(
                redirected_hoster
            )
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
    def _enforce_max_size(cache: dict[str, _CacheEntry]) -> None:
        """Evict oldest entries when cache exceeds ``_MAX_CACHE_SIZE``."""
        if len(cache) <= _MAX_CACHE_SIZE:
            return
        # Python dicts preserve insertion order; pop from the front
        excess = len(cache) - _MAX_CACHE_SIZE
        keys = list(cache.keys())[:excess]
        for k in keys:
            del cache[k]

    async def _resolve_with(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
        cache_key: str,
    ) -> ResolvedStream | None:
        """Resolve *url* with *resolver*; cache the outcome under *cache_key*."""
        result, cacheable = await self._try_resolver(resolver, hoster_name, url)
        if cacheable:
            self._cache_result(cache_key, result)
        return result

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
        """
        try:
            async with asyncio.timeout(self._resolve_timeout):
                result = await resolver.resolve(url)
            if (
                result is not None
                and self._verify_playback
                and self._http_client is not None
                and not await check_playable(self._http_client, result)
            ):
                log.warning("hoster_resolve_unplayable", hoster=hoster_name, url=url)
                return None, True
            if result is not None:
                log.info(
                    "hoster_resolve_success",
                    hoster=hoster_name,
                    is_hls=result.is_hls,
                )
                return result, True
            log.warning("hoster_resolve_failed", hoster=hoster_name, url=url)
        except (TimeoutError, httpx.TimeoutException):
            log.warning("hoster_resolve_timeout", hoster=hoster_name, url=url)
            return None, False
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
                self._redirect_cache[url] = _CacheEntry(
                    final_url,
                    _CACHE_TTL_REDIRECT,  # type: ignore[arg-type]
                )
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
