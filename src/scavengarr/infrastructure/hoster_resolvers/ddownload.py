"""DDownload hoster resolver — validates DDL links on ddownload.com / ddl.to.

DDownload is an XFileSharingPro-based file hosting service. URLs follow:
    https://ddownload.com/{file_id}
    https://ddownload.com/{file_id}/{filename}.html
    https://ddl.to/{file_id}

The file ID is a 12-character alphanumeric string.  Validation works by
fetching the file page and checking for offline markers.  File metadata
(name, size) is extracted from the HTML when available.

Active domains:
    ddownload.com  (main)
    ddl.to         (redirects to ddownload.com)

Dead domains (per JD2 2026-01):
    api.ddl.to
    esimpurcuesc.ddownload.com

Based on JD2 DdownloadCom.java (XFileSharingProBasic).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# Known DDownload domains (second-level part only, for registry matching).
_DOMAINS = {"ddownload", "ddl"}

# XFS file ID: 12 alphanumeric characters as first path segment.
_FILE_ID_RE = re.compile(r"^/([a-zA-Z0-9]{12})(?:/|$)")

# Offline markers from JD2 DdownloadCom.java + XFileSharingProBasic.
_OFFLINE_MARKERS = (
    "File Not Found",
    "file was removed",
    "file was banned by copyright",
    ">The file expired",
    ">The file was deleted",
)

# Filename: <h1 class="file-info-name">...</h1>
_FILENAME_RE = re.compile(
    r'<h1[^>]*class="file-info-name"[^>]*>([^<]+)</h1>',
    re.IGNORECASE,
)

# Filesize: <span class="file-size">4.0 GB</span>
_FILESIZE_RE = re.compile(
    r'class="file-size">([^<>]+)<',
    re.IGNORECASE,
)

# Fallback filesize: [<font ...>4.0 GB</font>]
_FILESIZE_FONT_RE = re.compile(
    r"\[<font[^>]*>(\d+[^<>]+)</font>\]",
)


def _extract_file_id(url: str) -> str | None:
    """Extract the XFS file ID from a ddownload URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        parts = hostname.removeprefix("www.").split(".")
        domain = parts[0] if len(parts) >= 2 else ""
        if domain not in _DOMAINS:
            return None
        match = _FILE_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class DDownloadResolver:
    """Resolves ddownload.com / ddl.to DDL links by checking file page HTML."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "ddownload"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a ddownload link by fetching the file page.

        Checks for XFS offline markers and copyright-ban markers to
        determine if the file is still available.  Returns a
        ResolvedStream with the canonical URL on success.
        """
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("ddownload_invalid_url", url=url)
            return None

        # Canonical URL always uses ddownload.com.
        canonical_url = f"https://ddownload.com/{file_id}"

        try:
            resp = await self._http.get(
                canonical_url,
                follow_redirects=True,
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("ddownload_request_failed", url=url)
            return None

        if resp.status_code == 404:
            log.info("ddownload_file_not_found", file_id=file_id)
            return None

        if resp.status_code != 200:
            log.warning(
                "ddownload_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        html = resp.text

        # Check for offline / copyright-ban markers.
        for marker in _OFFLINE_MARKERS:
            if marker in html:
                log.info(
                    "ddownload_file_offline",
                    file_id=file_id,
                    marker=marker,
                )
                return None

        # Check for redirect to error page.
        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info(
                "ddownload_error_redirect",
                file_id=file_id,
                url=final_url,
            )
            return None

        # Maintenance mode check (from JD2).
        if "This server is in maintenance mode" in html:
            log.warning("ddownload_maintenance", file_id=file_id)
            return None

        # Extract metadata for logging (best-effort).
        filename = _extract_filename(html)
        filesize = _extract_filesize(html)
        log.debug(
            "ddownload_resolved",
            file_id=file_id,
            filename=filename,
            filesize=filesize,
        )

        return ResolvedStream(
            video_url=canonical_url,
            quality=StreamQuality.UNKNOWN,
        )


def _extract_filename(html: str) -> str:
    """Extract filename from ddownload file page HTML."""
    match = _FILENAME_RE.search(html)
    return match.group(1).strip() if match else ""


def _extract_filesize(html: str) -> str:
    """Extract human-readable filesize from ddownload file page HTML."""
    match = _FILESIZE_RE.search(html)
    if match:
        return match.group(1).strip()
    match = _FILESIZE_FONT_RE.search(html)
    return match.group(1).strip() if match else ""
