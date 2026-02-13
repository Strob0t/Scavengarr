"""AlphaDDL hoster resolver — validates DDL links on alphaddl.com.

AlphaDDL is a DDL aggregator site. URLs follow the pattern:
    https://alphaddl.com/{slug}

The file ID is an alphanumeric slug from the path.

Domains:
    alphaddl.com  (current main)
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known AlphaDDL domains (second-level part only, for matching)
_DOMAINS = {"alphaddl"}

# File ID: alphanumeric slug with hyphens/underscores from path
_FILE_ID_RE = re.compile(r"^/([a-zA-Z0-9_-]+)")

# Offline markers
_OFFLINE_MARKERS = (
    "Page not found",
    "404",
    "not available",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the slug from an alphaddl URL."""
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
        slug = match.group(1)
        if len(slug) < 3:
            return None
        return slug
    except Exception:  # noqa: BLE001
        return None


class AlphaddlResolver:
    """Resolves alphaddl links by checking the page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "alphaddl"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate an alphaddl link by fetching the page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("alphaddl_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("alphaddl_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("alphaddl_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("alphaddl_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("alphaddl_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("alphaddl_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
