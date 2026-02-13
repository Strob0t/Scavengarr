"""Dropload hoster resolver — validates DDL links on dropload.io/tv.

Dropload is an XFileSharingPro-based file hosting service. URLs follow the pattern:
    https://dropload.io/{file_id}
    https://dropload.io/e/{file_id}
    https://dropload.io/d/{file_id}

The file ID is a 12-character alphanumeric string.

Domains (JD2 2025-12):
    dropload.io  (primary)
    dropload.tv

Based on JD2 DroploadIo.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Dropload domains (second-level part only, for matching)
_DOMAINS = {"dropload"}

# XFS file ID: 12 alphanumeric characters, optionally behind /e/, /d/, /embed-
_FILE_ID_RE = re.compile(r"^/(?:e/|d/|embed-)?([a-zA-Z0-9]{12})(?:/|$|\.html)")

# Offline markers from JD2 DroploadIo.java + XFileSharingProBasic
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    ">The file expired",
    ">The file was deleted",
    "File is gone",
    "File unavailable",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the XFS file ID from a dropload URL."""
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


class DroploadResolver:
    """Resolves dropload DDL links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "dropload"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a dropload link by fetching the file page.

        Checks for XFS offline markers to determine if the file is still
        available. Returns a ResolvedStream with the original URL on success.
        """
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("dropload_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(
                url,
                follow_redirects=True,
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("dropload_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                "dropload_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        html = resp.text

        # Check for offline markers
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("dropload_file_offline", file_id=file_id, marker=marker)
                return None

        # Check for redirect to error page
        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("dropload_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("dropload_resolved", file_id=file_id)
        return ResolvedStream(
            video_url=url,
            quality=StreamQuality.UNKNOWN,
        )
