"""ClicknUpload hoster resolver — validates DDL links on clicknupload.*.

ClicknUpload is an XFileSharingPro-based file hosting service. URLs follow:
    https://clicknupload.click/{file_id}
    https://clicknupload.click/{file_id}/{filename}.html

The file ID is a 12-character alphanumeric string.

Domains (JD2 data):
    clicknupload.click  (current main)
    clicknupload.to     (alias)
    clicknupload.cc     (alias)
    clicknupload.co     (alias)
    clicknupload.org    (alias)
    clickndownload.*    (aliases)

Dead: clicknupload.red, .link, .com, .club

Based on JD2 ClicknuploadOrg.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known ClicknUpload domains (second-level part only, for matching)
_DOMAINS = {"clicknupload", "clickndownload"}

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
    """Extract the XFS file ID from a clicknupload URL."""
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


class ClicknuploadResolver:
    """Resolves clicknupload links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "clicknupload"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a clicknupload link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("clicknupload_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("clicknupload_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("clicknupload_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("clicknupload_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("clicknupload_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("clicknupload_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
