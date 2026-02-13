"""Vidoza hoster resolver — validates DDL links on vidoza.net.

Vidoza is an XFileSharingPro-based video hosting service. URLs follow:
    https://vidoza.net/{file_id}.html
    https://vidoza.net/embed-{file_id}.html

The file ID is a 12-character alphanumeric string.

Domains (JD2 data):
    vidoza.net   (current main)
    vidoza.org   (alias)
    videzz.net   (alias)

Based on JD2 VidozaNet.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Vidoza domains (second-level part only, for matching)
_DOMAINS = {"vidoza", "videzz"}

# File ID: 12 alphanumeric chars, optionally prefixed with embed-
_FILE_ID_RE = re.compile(r"^/(?:embed-)?([a-zA-Z0-9]{12})(?:/|$|\.html)")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    "Reason for deletion:",
    "Conversion stage",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the XFS file ID from a vidoza URL."""
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


class VidozaResolver:
    """Resolves vidoza links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "vidoza"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a vidoza link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("vidoza_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("vidoza_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("vidoza_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("vidoza_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("vidoza_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("vidoza_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
