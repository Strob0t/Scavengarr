"""Uploaded hoster resolver — validates DDL links on uploaded.net / ul.to.

Uploaded is a file hosting service. URLs follow the pattern:
    https://uploaded.net/file/abc123
    https://ul.to/abc123

The file ID is a lowercase alphanumeric string.

Domains (JD2 data):
    uploaded.net  (current main)
    uploaded.to   (alias)
    ul.to         (short alias)

Based on JD2 UploadedNet.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Uploaded domains (second-level part only, for matching)
_DOMAINS = {"uploaded", "ul"}

# File ID: alphanumeric from path, /file/abc123 or /abc123
_FILE_ID_RE = re.compile(r"^/(?:file/)?([a-z0-9]+)$")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "File was deleted",
    "The requested file isn't available anymore",
    "File not found",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from an uploaded URL."""
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


class UploadedResolver:
    """Resolves uploaded.net links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "uploaded"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate an uploaded link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("uploaded_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("uploaded_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("uploaded_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("uploaded_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("uploaded_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("uploaded_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
