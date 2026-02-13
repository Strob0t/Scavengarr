"""NitroFlare hoster resolver — validates DDL links on nitroflare.com.

NitroFlare is a file hosting service. URLs follow the pattern:
    https://nitroflare.com/view/ABCDEF123
    https://nitro.download/view/ABCDEF123

The file ID is an uppercase alphanumeric string.

Domains (JD2 data):
    nitroflare.com   (current main)
    nitroflare.net   (alias)
    nitro.download   (alias)

Based on JD2 NitroFlareCom.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known NitroFlare domains (second-level part only, for matching)
_DOMAINS = {"nitroflare", "nitro"}

# File ID: uppercase alphanumeric after /view/ or /watch/
_FILE_ID_RE = re.compile(r"^/(?:view|watch)/([A-Z0-9]+)$")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "This file has been removed",
    ">File doesn't exist",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a nitroflare URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.split(".")
        domain = parts[-2] if len(parts) >= 2 else ""
        if domain not in _DOMAINS:
            return None
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class NitroflareResolver:
    """Resolves nitroflare links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "nitroflare"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a nitroflare link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("nitroflare_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("nitroflare_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("nitroflare_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("nitroflare_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("nitroflare_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("nitroflare_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
