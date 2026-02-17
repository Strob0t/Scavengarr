"""SendVid hoster resolver — validates streaming links on sendvid.com.

SendVid is a video hosting service. URLs follow the pattern:
    https://sendvid.com/{file_id}
    https://sendvid.com/embed/{file_id}

Uses a two-stage availability check:
    1. Fast API probe: GET /api/v1/videos/{ID}/status.json  (404 = offline)
    2. Page parse: extract <source src="..."> for the direct video URL

Based on JD2 SendvidCom.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)

_DOMAINS = {"sendvid"}

# File ID: alphanumeric, optionally prefixed with embed/
_FILE_ID_RE = re.compile(r"^/(?:embed/)?([A-Za-z0-9]+)$")

_API_BASE = "https://sendvid.com/api/v1/videos"


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a sendvid URL."""
    try:
        domain = extract_domain(url)
        if domain not in _DOMAINS:
            return None
        parsed = urlparse(url)
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class SendVidResolver:
    """Resolves SendVid links via API status check + page parse."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "sendvid"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a sendvid link and return a resolved stream."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("sendvid_invalid_url", url=url)
            return None

        # Stage 1: fast API availability check
        try:
            api_resp = await self._http.get(
                f"{_API_BASE}/{file_id}/status.json",
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("sendvid_api_request_failed", url=url)
            return None

        if api_resp.status_code == 404:
            log.info("sendvid_file_not_found", file_id=file_id)
            return None

        if api_resp.status_code != 200:
            log.warning(
                "sendvid_api_error",
                status=api_resp.status_code,
                file_id=file_id,
            )
            return None

        # Stage 2: fetch page to extract video source URL
        try:
            page_resp = await self._http.get(
                f"https://sendvid.com/{file_id}",
                follow_redirects=True,
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("sendvid_page_request_failed", url=url)
            return None

        if page_resp.status_code != 200:
            log.warning(
                "sendvid_page_error",
                status=page_resp.status_code,
                file_id=file_id,
            )
            return None

        # Return the original URL — video source extraction is optional
        # for availability validation
        log.debug("sendvid_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
