"""SerienStream hoster resolver — validates links on s.to / serienstream.*.

SerienStream is a streaming aggregator. URLs follow the pattern:
    https://s.to/serie/stream/show-name
    https://serienstream.to/serie/stream/show-name

This is a streaming aggregator, not a file host. For resolver purposes,
just validate URL is live.

Domains:
    s.to               (current short domain)
    serienstream.to    (alias)
    serien.sx          (alias)

NOTE: Domain matching uses "s" for s.to which is very short. We match
the full hostname for s.to to avoid false positives.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)

# Known SerienStream domains (second-level part only, for matching)
_DOMAINS = {"serienstream", "serien"}

# Full hostnames for short domain s.to
_FULL_HOSTNAMES = {"s.to", "www.s.to"}

# File ID: slug from /serie/ or /serien/ path
_FILE_ID_RE = re.compile(r"^/(?:serie|serien)/(.+)")

# Offline markers
_OFFLINE_MARKERS = (
    "Page not found",
    "Seite nicht gefunden",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the series slug from a serienstream URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        # Check full hostname for s.to
        if hostname in _FULL_HOSTNAMES:
            match = _FILE_ID_RE.search(parsed.path)
            return match.group(1) if match else None

        # Check second-level domain for other domains
        domain = extract_domain(url)
        if domain not in _DOMAINS:
            return None
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class SerienstreamResolver:
    """Resolves serienstream links by checking for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "serienstream"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a serienstream link by fetching the page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("serienstream_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("serienstream_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("serienstream_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("serienstream_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("serienstream_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("serienstream_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
