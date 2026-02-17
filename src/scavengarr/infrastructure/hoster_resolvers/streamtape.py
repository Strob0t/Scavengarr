"""Streamtape hoster resolver — extracts video URLs from streamtape.com.

Simple regex extraction: parse id/expires/ip/token parameters from the
embed page, build get_video URL. No JavaScript deobfuscation needed.
Based on JD2 StreamtapeCom.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Streamtape domains
_DOMAINS = {
    "streamtape",
    "strtape",
    "strcloud",
    "shavetape",
    "streamta",
    "strtpe",
    "streamadblocker",
    "tapeadvertisement",
    "watchadsontape",
    "tapecontent",
    "scloud",
    "strtapeadblock",
    "tapeblocker",
    "streamtapeadblockuser",
    "streamtapeadblock",
}


def _is_streamtape_domain(url: str) -> bool:
    """Check if URL belongs to a Streamtape domain."""
    try:
        hostname = urlparse(url).hostname or ""
        parts = hostname.split(".")
        domain = parts[-2] if len(parts) >= 2 else parts[0]
        return domain in _DOMAINS
    except Exception:  # noqa: BLE001
        return False


class StreamtapeResolver:
    """Resolves Streamtape embed pages to direct video URLs."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "streamtape"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Fetch Streamtape page and extract video download URL."""
        try:
            resp = await self._http.get(
                url,
                follow_redirects=True,
                timeout=15,
            )
            if resp.status_code != 200:
                log.warning(
                    "streamtape_http_error",
                    status=resp.status_code,
                    url=url,
                )
                return None

            html = resp.text
        except httpx.HTTPError:
            log.warning("streamtape_request_failed", url=url)
            return None

        # Check if video exists
        if ">Video not found" in html or resp.status_code in (404, 500):
            log.info("streamtape_video_not_found", url=url)
            return None

        # Extract parameters: id=...&expires=...&ip=...&token=...
        match = re.search(
            r"(id=[^\"'&]*&expires=\d+&ip=[^\"'&]*&token=[^\"'&]*?)([\"'<])",
            html,
        )
        if not match:
            log.warning("streamtape_no_params", url=url)
            return None

        params_str = match.group(1)

        # Try to get corrected token from JavaScript
        token_match = re.search(
            r"document\.getElementById[^<]*&token=([A-Z0-9\-_]+)",
            html,
        )
        if token_match:
            corrected_token = token_match.group(1)
            # Replace the token in params
            params_str = re.sub(
                r"token=[^&]*",
                f"token={corrected_token}",
                params_str,
            )

        # Determine base domain from the response URL
        resp_host = urlparse(str(resp.url)).hostname or "streamtape.com"

        video_url = f"https://{resp_host}/get_video?{params_str}&stream=1"

        log.debug("streamtape_resolved", video_url=video_url)
        return ResolvedStream(
            video_url=video_url,
            quality=StreamQuality.UNKNOWN,
            headers={"Referer": f"https://{resp_host}/"},
        )
