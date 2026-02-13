"""Registry that dispatches hoster URL resolution to per-hoster resolvers."""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.domain.ports.hoster_resolver import HosterResolverPort

log = structlog.get_logger(__name__)

# Cache TTLs for resolver results
_CACHE_TTL_ALIVE = 3600  # 1 hour for successful resolutions
_CACHE_TTL_DEAD = 900  # 15 minutes for failed resolutions
_CACHE_TTL_REDIRECT = 3600  # 1 hour for redirect mappings


def _extract_hoster_from_url(url: str) -> str:
    """Extract hoster name from URL domain.

    Examples:
        "https://voe.sx/e/abc" -> "voe"
        "https://streamtape.com/v/abc" -> "streamtape"
    """
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return ""
        parts = hostname.split(".")
        return parts[-2] if len(parts) >= 2 else parts[0]
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
    ) -> None:
        self._resolvers: dict[str, HosterResolverPort] = {}
        self._http_client = http_client
        self._resolve_timeout = resolve_timeout
        self._result_cache: dict[str, _CacheEntry] = {}
        self._redirect_cache: dict[str, _CacheEntry] = {}
        for resolver in resolvers or []:
            self.register(resolver)

    def register(self, resolver: HosterResolverPort) -> None:
        """Register a resolver for a specific hoster."""
        self._resolvers[resolver.name] = resolver
        log.debug("hoster_resolver_registered", hoster=resolver.name)

    @property
    def supported_hosters(self) -> list[str]:
        """Return list of hosters with registered resolvers."""
        return list(self._resolvers.keys())

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
        6. Cache the result (alive or dead) and return.
        """
        # 0. Check result cache
        cached = self._result_cache.get(url)
        if cached is not None and not cached.is_expired:
            log.debug("hoster_resolve_cache_hit", url=url)
            return cached.value

        # URL domain is authoritative; fall back to plugin-provided hint
        hoster_name = _extract_hoster_from_url(url) or hoster

        # 1. Try specific resolver for URL domain
        resolver = self._resolvers.get(hoster_name)
        if resolver is not None:
            result = await self._try_resolver(resolver, hoster_name, url)
            self._cache_result(url, result)
            return result

        # 2. No resolver for this domain — try following redirects
        final_url = await self._follow_redirects(url)
        if final_url:
            redirected_hoster = _extract_hoster_from_url(final_url)
            resolver = self._resolvers.get(redirected_hoster)
            if resolver is not None:
                log.info(
                    "hoster_resolve_after_redirect",
                    original=hoster_name,
                    redirected=redirected_hoster,
                    url=final_url,
                )
                result = await self._try_resolver(
                    resolver, redirected_hoster, final_url
                )
                self._cache_result(url, result)
                return result

        # 3. Try hoster hint if different from URL domain
        #    Handles rotating redirect domains (e.g., lauradaydo.com for VOE)
        if hoster and hoster != hoster_name:
            resolver = self._resolvers.get(hoster)
            if resolver is not None:
                log.info(
                    "hoster_resolve_via_hint",
                    hint=hoster,
                    url_domain=hoster_name,
                    url=url,
                )
                result = await self._try_resolver(resolver, hoster, url)
                self._cache_result(url, result)
                return result

        # 4. Fallback: content-type probing
        result = await self._probe_content_type(url, hoster_name)
        self._cache_result(url, result)
        return result

    def _cache_result(self, url: str, result: ResolvedStream | None) -> None:
        """Cache a resolution result with appropriate TTL."""
        ttl = _CACHE_TTL_ALIVE if result is not None else _CACHE_TTL_DEAD
        self._result_cache[url] = _CacheEntry(result, ttl)

    async def _try_resolver(
        self,
        resolver: HosterResolverPort,
        hoster_name: str,
        url: str,
    ) -> ResolvedStream | None:
        """Attempt resolution with a specific resolver, logging success/failure."""
        try:
            result = await resolver.resolve(url)
            if result is not None:
                log.info(
                    "hoster_resolve_success",
                    hoster=hoster_name,
                    is_hls=result.is_hls,
                )
                return result
            log.warning("hoster_resolve_failed", hoster=hoster_name, url=url)
        except httpx.TimeoutException:
            log.warning("hoster_resolve_timeout", hoster=hoster_name, url=url)
        except httpx.HTTPError as exc:
            log.warning(
                "hoster_resolve_http_error",
                hoster=hoster_name,
                url=url,
                error=str(exc),
            )
        except Exception:
            log.exception("hoster_resolve_error", hoster=hoster_name, url=url)
        return None

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
