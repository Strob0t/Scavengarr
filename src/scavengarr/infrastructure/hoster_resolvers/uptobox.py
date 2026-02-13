"""Uptobox hoster resolver — validates DDL links on uptobox.com.

Uptobox is a file hosting service. URLs follow the pattern:
    https://uptobox.com/{file_id}
    https://uptostream.com/{file_id}

The file ID is a 12-character alphanumeric string.

Domains:
    uptobox.com     (current main)
    uptostream.com  (streaming alias)

NOTE: Site is frequently offline/changing domains. Simple XFS-like check.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Uptobox domains (second-level part only, for matching)
_DOMAINS = {"uptobox", "uptostream"}

# File ID: 12 alphanumeric characters as first path segment
_FILE_ID_RE = re.compile(r"^/([a-zA-Z0-9]{12})(?:/|$)")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "File has been removed",
    "This file is deleted",
    "This page is not available",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from an uptobox URL."""
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


class UptoboxResolver:
    """Resolves uptobox links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "uptobox"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate an uptobox link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("uptobox_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("uptobox_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("uptobox_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("uptobox_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("uptobox_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("uptobox_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
