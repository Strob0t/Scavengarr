"""FSST hoster resolver — validates DDL links on fsst.online.

FSST is a video hosting service. URLs follow the pattern:
    https://fsst.online/{file_id}
    https://fsst.online/e/{file_id}
    https://fsst.online/d/{file_id}

The file ID is an alphanumeric string from the path.

Domains:
    fsst.online  (current main)
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known FSST domains (second-level part only, for matching)
_DOMAINS = {"fsst"}

# File ID: alphanumeric from path, optionally prefixed with e/ or d/
_FILE_ID_RE = re.compile(r"^/(?:e/|d/)?([a-zA-Z0-9]+)$")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    "Video not found",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a fsst URL."""
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


class FsstResolver:
    """Resolves fsst links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "fsst"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a fsst link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("fsst_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("fsst_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("fsst_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("fsst_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("fsst_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("fsst_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
