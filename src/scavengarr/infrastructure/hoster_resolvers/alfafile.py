"""Alfafile hoster resolver — validates DDL links on alfafile.net.

Alfafile is a file hosting service. URLs follow the pattern:
    https://alfafile.net/file/abc123

The file ID is an alphanumeric string from the path.

Domains (JD2 data):
    alfafile.net  (current main)

Based on JD2 AlfafileNet.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Alfafile domains (second-level part only, for matching)
_DOMAINS = {"alfafile"}

# File ID: alphanumeric from path /file/abc123
_FILE_ID_RE = re.compile(r"^/file/([A-Za-z0-9]+)$")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    "doesn't exist",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from an alfafile URL."""
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


class AlfafileResolver:
    """Resolves alfafile links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "alfafile"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate an alfafile link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("alfafile_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("alfafile_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("alfafile_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("alfafile_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("alfafile_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("alfafile_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
