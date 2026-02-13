"""FileCrypt hoster resolver — validates container links on filecrypt.cc.

FileCrypt is a link container/shortener service. URLs follow the pattern:
    https://filecrypt.cc/Container/ABC123

The file ID is an alphanumeric string from the /Container/ path.

Domains:
    filecrypt.cc  (current main)

NOTE: This is a link container, not a direct file host. Simple availability check.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known FileCrypt domains (second-level part only, for matching)
_DOMAINS = {"filecrypt"}

# File ID: alphanumeric from /Container/ path
_FILE_ID_RE = re.compile(r"^/Container/([A-Za-z0-9]+)")

# Offline markers
_OFFLINE_MARKERS = (
    "File Not Found",
    "Container not found",
    "has been deleted",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the container ID from a filecrypt URL."""
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


class FilecryptResolver:
    """Resolves filecrypt container links by checking for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "filecrypt"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a filecrypt container link by fetching the page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("filecrypt_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("filecrypt_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("filecrypt_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("filecrypt_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("filecrypt_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("filecrypt_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
