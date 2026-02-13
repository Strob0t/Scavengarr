"""1fichier hoster resolver — validates DDL links on 1fichier.com.

1fichier is a file hosting service with many alias domains. URLs follow:
    https://1fichier.com/?abc12345
    https://alterupload.com/?abc12345

The file ID is a 5-20 char alphanumeric string from the query part.

Domains (JD2 data):
    1fichier       (current main)
    alterupload    (alias)
    cjoint         (alias)
    desfichiers    (alias)
    dfichiers      (alias)
    megadl         (alias)
    mesfichiers    (alias)
    piecejointe    (alias)
    pjointe        (alias)
    tenvoi         (alias)
    dl4free        (alias)

Based on JD2 OneFichierCom.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known 1fichier domains (second-level part only, for matching)
_DOMAINS = {
    "1fichier",
    "alterupload",
    "cjoint",
    "desfichiers",
    "dfichiers",
    "megadl",
    "mesfichiers",
    "piecejointe",
    "pjointe",
    "tenvoi",
    "dl4free",
}

# File ID: 5-20 char alphanumeric from query string (after ?)
_FILE_ID_RE = re.compile(r"^\?([a-z0-9]{5,20})$")

# Offline markers
_OFFLINE_MARKERS = (
    "not found",
    "has been deleted",
    "File not found",
    "The requested file could not be found",
    "The requested file has been deleted",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a 1fichier URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.split(".")
        domain = parts[-2] if len(parts) >= 2 else ""
        if domain not in _DOMAINS:
            return None
        # 1fichier puts the file ID in the query: /?abc12345
        query = parsed.query
        if query:
            match = re.match(r"^([a-z0-9]{5,20})$", query)
            return match.group(1) if match else None
        # Also try path for /?abc12345 format (path = "/", query = "abc12345")
        return None
    except Exception:  # noqa: BLE001
        return None


class OnefichierResolver:
    """Resolves 1fichier links by checking the file page for offline markers."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "1fichier"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a 1fichier link by fetching the file page."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("onefichier_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("onefichier_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning("onefichier_http_error", status=resp.status_code, url=url)
            return None

        html = resp.text
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info("onefichier_file_offline", file_id=file_id, marker=marker)
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info("onefichier_error_redirect", file_id=file_id, url=final_url)
            return None

        log.debug("onefichier_resolved", file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
