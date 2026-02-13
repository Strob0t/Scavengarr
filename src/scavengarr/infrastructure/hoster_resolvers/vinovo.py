"""Vinovo hoster resolver — validates video links on vinovo.to/si.

Vinovo is an XFileSharingPro-derived video hosting service. URLs follow:
    https://vinovo.to/e/{file_id}
    https://vinovo.to/d/{file_id}

The file ID is a 12+ character alphanumeric string.

Domains (JD2 2025-12):
    vinovo.to  (primary)
    vinovo.si

Based on JD2 VinovoTo.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

_DOMAINS = {"vinovo"}

# Vinovo uses /d/ or /e/ prefix with 12+ char alphanumeric IDs
_FILE_ID_RE = re.compile(r"^/(?:e/|d/)([a-zA-Z0-9]{12,})(?:/|$)")

_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    ">The file expired",
    ">The file was deleted",
    "File is gone",
    "File unavailable",
    "Video not found",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a vinovo URL."""
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


class VinovoResolver:
    """Resolves vinovo links by checking the page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "vinovo"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a vinovo link by fetching the page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("vinovo_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(
                url, follow_redirects=True, timeout=15
            )
        except httpx.HTTPError:
            log.warning("vinovo_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                "vinovo_http_error", status=resp.status_code, url=url
            )
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info(
                    "vinovo_file_offline", file_id=file_id, marker=marker
                )
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info(
                "vinovo_error_redirect", file_id=file_id, url=final_url
            )
            return None

        log.debug("vinovo_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
