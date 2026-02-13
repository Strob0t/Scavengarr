"""TurboBit hoster resolver — validates DDL links on turbobit.net.

TurboBit is a file hosting service. URLs follow the pattern:
    https://turbobit.net/{file_id}.html
    https://turbobit.net/download/free/{file_id}
    https://turb.to/{file_id}.html

The file ID is extracted from the URL path.

Domains (JD2 data):
    turbobit.net  (current main)
    turb.to       (alias)
    turbo.to      (alias)

Dead domains: turbobbit.com, ifolder.com.ua

Based on JD2 TurboBitNet.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known TurboBit domains (second-level part only, for matching)
_DOMAINS = {"turbobit", "turb", "turbo"}

# File ID: alphanumeric string from first path segment or after /download/free/
_FILE_ID_RE = re.compile(r"^/(?:download/free/)?([A-Za-z0-9]+?)(?:/|\.html|$)")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    "File was not found",
    ">This document is not available",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a turbobit URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.split(".")
        domain = parts[-2] if len(parts) >= 2 else ""
        if domain not in _DOMAINS:
            return None
        match = _FILE_ID_RE.search(parsed.path)
        if not match:
            return None
        fid = match.group(1)
        if len(fid) < 6:
            return None
        return fid
    except Exception:  # noqa: BLE001
        return None


class TurbobitResolver:
    """Resolves turbobit links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "turbobit"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a turbobit link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("turbobit_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("turbobit_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("turbobit_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("turbobit_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("turbobit_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("turbobit_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
