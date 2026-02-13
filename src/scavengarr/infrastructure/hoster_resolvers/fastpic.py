"""FastPic hoster resolver — validates image links on fastpic.org / fastpic.ru.

FastPic is an image hosting service. URLs follow the pattern:
    https://fastpic.org/view/123/2025/0101/abc123def456.jpg.html
    https://fastpic.ru/fullview/123/2025/abc123.png

This is an image host — availability is checked via a simple HEAD/GET request.

Domains (JD2 data):
    fastpic.org  (current main)
    fastpic.ru   (alias)

Based on JD2 FastPicRu.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known FastPic domains (second-level part only, for matching)
_DOMAINS = {"fastpic"}

# File ID: hash from path (32-char hex + extension)
_FILE_ID_RE = re.compile(r"/(?:full)?view/.+?([a-f0-9]{32}\.[A-Za-z]+)")

# Offline markers
_OFFLINE_MARKERS = (
    "not_found",
    "Image not found",
    "404 Not Found",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a fastpic URL."""
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


class FastpicResolver:
    """Resolves fastpic links by checking image availability."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "fastpic"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a fastpic link by fetching the image page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("fastpic_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("fastpic_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("fastpic_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("fastpic_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("fastpic_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("fastpic_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
