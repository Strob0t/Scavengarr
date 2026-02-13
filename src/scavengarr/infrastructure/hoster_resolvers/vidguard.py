"""Vidguard hoster resolver — validates video links on vidguard.to + aliases.

Vidguard is a custom (non-XFS) video hosting service with many domain
aliases and variable-length alphanumeric file IDs.

URL patterns:
    https://vidguard.to/e/{file_id}
    https://vidguard.to/d/{file_id}
    https://vidguard.to/v/{file_id}

Domains (JD2 2025-12):
    vidguard.to      (primary)
    vid-guard.com
    vgfplay.com
    vgembed.com
    v6embed.xyz
    vembed.net
    bembed.net
    listeamed.net
    moflix-stream.day

Based on JD2 VidguardTo.java (custom PluginForHost).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

_DOMAINS = {
    "vidguard",
    "vid-guard",
    "vgfplay",
    "vgembed",
    "v6embed",
    "vembed",
    "bembed",
    "listeamed",
    "moflix-stream",
}

# Variable-length alphanumeric IDs with /d/, /e/, or /v/ prefix
_FILE_ID_RE = re.compile(r"^/(?:d|e|v)/([A-Za-z0-9]+)$")

_OFFLINE_MARKERS = (
    "File Not Found",
    "Video not found",
    "video you are looking for is not found",
    "err:1002",
    "File unavailable",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a vidguard URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.split(".")

        # Handle multi-part second-level domains (e.g. vid-guard.com,
        # moflix-stream.day)
        if len(parts) >= 2:
            domain = parts[-2]
        else:
            return None

        if domain not in _DOMAINS:
            return None

        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class VidguardResolver:
    """Resolves vidguard links by checking the page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "vidguard"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a vidguard link by fetching the page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("vidguard_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(
                url, follow_redirects=True, timeout=15
            )
        except httpx.HTTPError:
            log.warning("vidguard_request_failed", url=url)
            return None

        if resp.status_code in {404, 403}:
            log.info(
                "vidguard_http_offline",
                file_id=file_id,
                status=resp.status_code,
            )
            return None

        if resp.status_code != 200:
            log.warning(
                "vidguard_http_error", status=resp.status_code, url=url
            )
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info(
                    "vidguard_file_offline", file_id=file_id, marker=marker
                )
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info(
                "vidguard_error_redirect", file_id=file_id, url=final_url
            )
            return None

        log.debug("vidguard_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
