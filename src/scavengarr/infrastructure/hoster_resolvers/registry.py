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

        1. Try the specific hoster resolver if registered.
        2. Fall back to content-type probing (HEAD request).
        3. Return None if resolution fails.
        """
        hoster_name = hoster or _extract_hoster_from_url(url)

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

        # Fallback: content-type probing
        return await self._probe_content_type(url, hoster_name)

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
