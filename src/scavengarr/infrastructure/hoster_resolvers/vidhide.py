"""VidHide hoster resolver — validates DDL links on vidhide.com and aliases.

VidHide is an XFileSharingPro-based video hosting service with many domains.
URLs follow:
    https://vidhide.com/{file_id}
    https://vidhide.com/embed-{file_id}.html
    https://filelions.to/f/{file_id}

The file ID is a 12-character lowercase alphanumeric string.

Domains (JD2 data — alive subset):
    vidhide.com       (current main)
    vidhidepro.com    (alias)
    vidhidehub.com    (alias)
    filelions.to      (alias)
    vidhideplus.com   (alias)
    vidhidefast.com   (alias)

Based on JD2 VidhideCom.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known VidHide domains — alive subset (second-level part only, for matching)
_DOMAINS = {"vidhide", "vidhidepro", "vidhidehub", "filelions", "vidhideplus", "vidhidefast"}

# File ID: 12-char lowercase alphanumeric with various path prefixes
_FILE_ID_RE = re.compile(
    r"^/(?:embed-|embed/|e/|f/|v/|d/|file/)?([a-z0-9]{12})(?:/|$|\.html)"
)

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    "Video embed restricted",
    "Downloads disabled",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the XFS file ID from a vidhide URL."""
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


class VidhideResolver:
    """Resolves vidhide links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "vidhide"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a vidhide link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("vidhide_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("vidhide_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("vidhide_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("vidhide_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("vidhide_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("vidhide_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
