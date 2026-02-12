"""Rapidgator.net hoster resolver — validates DDL links via website scraping.

Rapidgator is a file hosting service. URLs follow the pattern:
    https://rapidgator.net/file/{id}
    https://rapidgator.net/file/{id}/filename.html

Supported domains: rapidgator.net, rapidgator.asia, rg.to

The resolver fetches the file page (no auth required) and checks:
- HTTP 404 or "404 File not found" → file offline
- Redirect away from file ID → file offline
- Extracts filename, filesize, and MD5 when available

Based on JD2 RapidGatorNet.java website check logic.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Domains recognised as Rapidgator mirrors.
_DOMAINS = {"rapidgator.net", "rapidgator.asia", "rg.to"}

# File ID: 32-char hex hash OR numeric ID.
_FILE_ID_RE = re.compile(r"/file/([a-z0-9]{32}|\d+)")

# Website scraping patterns (from JD2 RapidGatorNet.java).
_FILENAME_RE = re.compile(
    r"Downloading\s*:\s*</strong>\s*<a[^>]*>([^<>\"]+)<",
    re.IGNORECASE,
)
_FILENAME_TITLE_RE = re.compile(
    r"<title>\s*Download file\s*([^<>\"]+)</title>",
    re.IGNORECASE,
)
_FILESIZE_RE = re.compile(
    r"File size:\s*<strong>([^<>\"]+)</strong>",
    re.IGNORECASE,
)
_OFFLINE_RE = re.compile(r">\s*404 File not found", re.IGNORECASE)


def _extract_file_id(url: str) -> str | None:
    """Extract the file ID from a Rapidgator URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Strip optional www. prefix.
        bare = hostname.removeprefix("www.")
        if bare not in _DOMAINS:
            return None
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class RapidgatorResolver:
    """Resolves Rapidgator DDL links by checking file availability via website."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "rapidgator"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a Rapidgator link and return the canonical download URL.

        Fetches the file page (no auth) and checks for offline markers.
        Returns a ResolvedStream with the canonical URL if the file is online.
        """
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("rapidgator_invalid_url", url=url)
            return None

        canonical_url = f"https://rapidgator.net/file/{file_id}"

        try:
            resp = await self._http.get(
                canonical_url,
                follow_redirects=True,
                timeout=15,
                headers={"Accept-Language": "en-US,en;q=0.8"},
            )
        except httpx.HTTPError:
            log.warning("rapidgator_request_failed", url=url)
            return None

        if resp.status_code == 404:
            log.info("rapidgator_file_not_found", file_id=file_id)
            return None

        if resp.status_code != 200:
            log.warning(
                "rapidgator_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        html = resp.text

        # Offline check: HTML contains "404 File not found".
        if _OFFLINE_RE.search(html):
            log.info("rapidgator_file_not_found", file_id=file_id)
            return None

        # Offline check: redirected away from file page (no file_id in final URL).
        final_url = str(resp.url)
        if file_id not in final_url:
            log.info(
                "rapidgator_redirect_away",
                file_id=file_id,
                final_url=final_url,
            )
            return None

        # Extract metadata for logging (best-effort).
        filename = _extract_filename(html)
        filesize = _extract_filesize(html)
        log.debug(
            "rapidgator_resolved",
            file_id=file_id,
            filename=filename,
            filesize=filesize,
        )

        return ResolvedStream(
            video_url=canonical_url,
            quality=StreamQuality.UNKNOWN,
        )


def _extract_filename(html: str) -> str:
    """Extract filename from Rapidgator file page HTML."""
    match = _FILENAME_RE.search(html)
    if match:
        return match.group(1).strip()
    match = _FILENAME_TITLE_RE.search(html)
    if match:
        return match.group(1).strip()
    return ""


def _extract_filesize(html: str) -> str:
    """Extract human-readable filesize from Rapidgator file page HTML."""
    match = _FILESIZE_RE.search(html)
    return match.group(1).strip() if match else ""
