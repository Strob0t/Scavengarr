"""Filestore hoster resolver — validates DDL links on filestore.me.

Filestore is an XFileSharingPro-based file hosting service. URLs follow:
    https://filestore.me/{file_id}
    https://filestore.me/{file_id}/{filename}.html

The file ID is a 12-character alphanumeric string.

Domains (JD2 data):
    filestore.me  (current main)

Based on JD2 FilestoreMe.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Filestore domains (second-level part only, for matching)
_DOMAINS = {"filestore"}

# XFS file ID: 12 alphanumeric characters as first path segment
_FILE_ID_RE = re.compile(r"^/([a-zA-Z0-9]{12})(?:/|$)")

# Offline markers — standard XFS
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    ">The file expired",
    ">The file was deleted",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the XFS file ID from a filestore URL."""
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


class FilestoreResolver:
    """Resolves filestore links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "filestore"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a filestore link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("filestore_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("filestore_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("filestore_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("filestore_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("filestore_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("filestore_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
