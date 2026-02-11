"""Registry that dispatches hoster URL resolution to per-hoster resolvers."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.domain.ports.hoster_resolver import HosterResolverPort

log = structlog.get_logger(__name__)


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


class HosterResolverRegistry:
    """Dispatches hoster URL resolution to the appropriate resolver.

    Falls back to content-type probing when no specific resolver is registered.
    """

    def __init__(
        self,
        resolvers: list[HosterResolverPort] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._resolvers: dict[str, HosterResolverPort] = {}
        self._http_client = http_client
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

    async def resolve(self, url: str, hoster: str = "") -> ResolvedStream | None:
        """Resolve a hoster embed URL to a playable video URL.

        1. Try the specific hoster resolver (URL domain takes priority over hint).
        2. If URL domain has no resolver, follow HTTP redirects and retry.
        3. Fall back to content-type probing (HEAD request).
        4. Return None if resolution fails.
        """
        # URL domain is authoritative; fall back to plugin-provided hint
        hoster_name = _extract_hoster_from_url(url) or hoster

        # Try specific resolver
        resolver = self._resolvers.get(hoster_name)
        if resolver is not None:
            log.info(
                "hoster_resolve_start",
                hoster=hoster_name,
                url=url,
            )
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
            except Exception:
                log.exception("hoster_resolve_error", hoster=hoster_name, url=url)
            return None

        # No resolver for this domain — try following redirects
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
                try:
                    result = await resolver.resolve(final_url)
                    if result is not None:
                        log.info(
                            "hoster_resolve_success",
                            hoster=redirected_hoster,
                            is_hls=result.is_hls,
                        )
                        return result
                    log.warning(
                        "hoster_resolve_failed",
                        hoster=redirected_hoster,
                        url=final_url,
                    )
                except Exception:
                    log.exception(
                        "hoster_resolve_error",
                        hoster=redirected_hoster,
                        url=final_url,
                    )
                return None

        # Fallback: content-type probing
        return await self._probe_content_type(url, hoster_name)

    async def _follow_redirects(self, url: str) -> str | None:
        """Follow HTTP redirects and return final URL if domain changed.

        Used for redirect-based URLs like cine.to/out/{id} that redirect
        to actual hoster embed URLs (e.g., voe.sx/e/abc).
        """
        if self._http_client is None:
            return None
        try:
            resp = await self._http_client.head(url, follow_redirects=True, timeout=10)
            final_url = str(resp.url)
            if final_url != url:
                log.debug(
                    "hoster_redirect_followed",
                    original=url,
                    final=final_url,
                )
                return final_url
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
            resp = await self._http_client.head(url, follow_redirects=True, timeout=10)
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

        except Exception:
            log.debug(
                "hoster_probe_failed",
                hoster=hoster_name,
                url=url,
            )

        return None
