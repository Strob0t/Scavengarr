"""Go4Up hoster resolver — validates DDL links on go4up.com.

Go4Up is a multi-host upload/link service. URLs follow the pattern:
    https://go4up.com/dl/abc123
    https://go4up.com/link/abc123

The file ID is an alphanumeric string from the path.

Domains:
    go4up.com  (current main)
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known Go4Up domains (second-level part only, for matching)
_DOMAINS = {"go4up"}

# File ID: alphanumeric from /dl/ or /link/ path
_FILE_ID_RE = re.compile(r"^/(?:dl|link)/([a-zA-Z0-9]+)")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "Link not found",
    "has been removed",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a go4up URL."""
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


class Go4upResolver:
    """Resolves go4up links by checking the link page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "go4up"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a go4up link by fetching the link page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("go4up_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("go4up_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("go4up_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("go4up_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("go4up_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("go4up_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
