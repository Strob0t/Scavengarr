"""Filemoon hoster resolver — extracts HLS URLs from filemoon.sx embed pages.

Supports two page generations:
1. Byse player (current): a Vite/React app whose stream URL exists only once
   the player runs — "click play to verify you're a human" starts a
   fingerprint attestation plus a proof-of-work captcha, then an encrypted
   playback response. The stream request is captured from the stealth
   browser (``StealthPool.capture_media``) instead of replaying that flow.
2. Legacy XFS: packed JavaScript (Dean Edwards packer) with a JWPlayer config,
   or a plain HLS URL in the page source.

Filemoon domain variants: filemoon.sx, filemoon.to, filemoon.eu, etc.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers._video_extract import (
    extract_hls_from_unpacked,
    unpack_p_a_c_k,
)

if TYPE_CHECKING:
    from scavengarr.infrastructure.browser.stealth_pool import StealthPool

log = structlog.get_logger(__name__)

_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Navigation + Cloudflare budget of the browser capture (the player clicks
# after it are bounded by StealthPool's own waits)
_CAPTURE_TIMEOUT_S = 15.0


def _unpack_p_a_c_k(packed: str) -> str | None:
    """Unpack Dean Edwards packed JavaScript (delegates to shared module)."""
    return unpack_p_a_c_k(packed)


def _extract_hls_from_unpacked(js: str) -> str | None:
    """Extract HLS URL from unpacked JWPlayer config (delegates to shared module)."""
    return extract_hls_from_unpacked(js)


class FilemoonResolver:
    """Resolves Filemoon embed pages to playable HLS URLs.

    Supports filemoon.sx, filemoon.to and domain variants. Legacy pages are
    parsed from the HTML; Byse player pages need *stealth_pool* (without it
    they cannot be resolved).
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        stealth_pool: StealthPool | None = None,
    ) -> None:
        self._http = http_client
        self._stealth_pool = stealth_pool

    @property
    def name(self) -> str:
        return "filemoon"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Parse a legacy embed page, else capture the Byse player's stream."""
        embed_url = self._normalize_embed_url(url)

        try:
            resp = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": _BROWSER_UA},
            )
        except httpx.HTTPError:
            log.warning("filemoon_request_failed", url=embed_url)
            return None

        if resp.status_code == 200:
            html = resp.text
            if self._is_offline(html):
                log.info("filemoon_offline", url=url)
                return None
            result = self._try_packed_js(html) or self._try_direct_hls(html)
            if result:
                return ResolvedStream(
                    video_url=result.video_url,
                    is_hls=result.is_hls,
                    quality=result.quality,
                    # Referer after redirects: the CDN checks it
                    headers={"Referer": str(resp.url)},
                )
        else:
            # Cloudflare / geo block: the browser may still get through
            log.info("filemoon_http_error", status=resp.status_code, url=embed_url)

        return await self._capture_player_stream(embed_url)

    async def _capture_player_stream(self, embed_url: str) -> ResolvedStream | None:
        """Run the player in the stealth browser and take its stream request."""
        if self._stealth_pool is None:
            log.warning("filemoon_extraction_failed", url=embed_url, browser=False)
            return None
        media = await self._stealth_pool.capture_media(
            embed_url, timeout=_CAPTURE_TIMEOUT_S
        )
        if media is None:
            log.warning("filemoon_extraction_failed", url=embed_url, browser=True)
            return None
        log.debug("filemoon_player_stream", url=media.url[:80])
        return ResolvedStream(
            video_url=media.url,
            is_hls=".m3u8" in media.url,
            quality=StreamQuality.UNKNOWN,
            headers={"Referer": media.referer or embed_url},
        )

    def _try_packed_js(self, html: str) -> ResolvedStream | None:
        """Extract HLS URL from packed JavaScript blocks.

        Uses a robust start-marker approach: find eval(function(p,a,c,k,e,d))
        start positions, extract a chunk, and let _unpack_p_a_c_k() handle
        parameter extraction. This avoids fragile full-block regex matching
        that breaks on real-world Filemoon page variations.
        """
        for m in re.finditer(
            r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)",
            html,
        ):
            # Extract a chunk large enough to contain the full packed block
            chunk = html[m.start() : m.start() + 65536]
            unpacked = _unpack_p_a_c_k(chunk)
            if not unpacked:
                continue

            hls_url = _extract_hls_from_unpacked(unpacked)
            if hls_url:
                log.debug("filemoon_packed_js_success", url=hls_url[:80])
                return ResolvedStream(
                    video_url=hls_url,
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )

        return None

    def _try_direct_hls(self, html: str) -> ResolvedStream | None:
        """Look for HLS URL directly in page source."""
        match = re.search(
            r"""["'](https?://[^"']+\.m3u8[^"']*)["']""",
            html,
        )
        if match:
            url = match.group(1)
            if "thumbnail" not in url.lower() and "track" not in url.lower():
                log.debug("filemoon_direct_hls", url=url[:80])
                return ResolvedStream(
                    video_url=url,
                    is_hls=True,
                    quality=StreamQuality.UNKNOWN,
                )
        return None

    def _normalize_embed_url(self, url: str) -> str:
        """Ensure URL uses the /e/ embed format."""
        if "/e/" in url:
            return url
        # Convert /d/ or /download/ to /e/
        url = re.sub(r"/(?:d|download)/", "/e/", url)
        return url

    def _is_offline(self, html: str) -> bool:
        """Check for Filemoon offline/removed markers."""
        if "File Not Found" in html:
            return True
        if "file was deleted" in html.lower():
            return True
        if 'class="fake-signup"' in html:
            return True
        return False
