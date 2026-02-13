"""MixDrop hoster resolver — validates DDL links on mixdrop.ag.

MixDrop is a video hosting service with many alias domains. URLs follow:
    https://mixdrop.ag/f/abc123
    https://mixdrop.ag/e/abc123
    https://mixdrop.ag/emb/abc123

The file ID is a lowercase alphanumeric string from the path.

Domains (JD2 data — alive subset):
    mixdrop.ag    (current main)
    mxdrop.*      (alias)
    m1xdrop.*     (alias)
    mixdrop23.*   (alias)

Dead: mixdrop.co, .bz, .sx, .to, .vc, .is, .ms

Based on JD2 MixdropCo.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known MixDrop domains — alive subset (second-level part only, for matching)
_DOMAINS = {"mixdrop", "mxdrop", "m1xdrop", "mixdrop23"}

# File ID: lowercase alphanumeric from /f/, /e/, or /emb/ path
_FILE_ID_RE = re.compile(r"^/(?:f|e|emb)/([a-z0-9]+)$")

# Offline markers
_OFFLINE_MARKERS = (
    "/imgs/illustration-notfound.png",
    "File not found",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a mixdrop URL."""
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


class MixdropResolver:
    """Resolves mixdrop links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "mixdrop"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a mixdrop link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("mixdrop_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("mixdrop_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("mixdrop_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("mixdrop_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("mixdrop_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("mixdrop_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
