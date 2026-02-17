"""Mediafire hoster resolver — validates DDL links via the public file API.

Mediafire is a file hosting service. URLs follow several patterns:
    https://www.mediafire.com/file/{quickkey}/{filename}/file
    https://www.mediafire.com/file/{quickkey}
    https://www.mediafire.com/download/{quickkey}
    https://www.mediafire.com/view/{quickkey}
    https://www.mediafire.com/?{quickkey}

The public file info API (no auth required for public files):
    GET https://www.mediafire.com/api/1.5/file/get_info.php?quick_key={ID}&response_format=json
    → {"response": {"result": "Success", "file_info": {filename, size, hash, ...}}}

Based on JD2 MediafireCom.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

_DOMAINS = {"mediafire"}

# File ID (quickkey): alphanumeric, extracted from path or query string
_FILE_ID_RE = re.compile(r"^/(?:file|download|view)/([a-z0-9]+)")
_QUERY_ID_RE = re.compile(r"^\?([a-z0-9]+)$")

_API_URL = "https://www.mediafire.com/api/1.5/file/get_info.php"

# API error codes that indicate file is offline
_OFFLINE_ERROR_CODES = {110, 111}


def _extract_file_id(url: str) -> str | None:
    """Extract the quickkey file ID from a Mediafire URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if "mediafire" not in hostname:
            return None

        # Try path-based patterns: /file/{id}, /download/{id}, /view/{id}
        match = _FILE_ID_RE.search(parsed.path)
        if match:
            return match.group(1)

        # Try query-based pattern: /?{id}
        if parsed.path in ("/", "") and parsed.query:
            qmatch = _QUERY_ID_RE.search(f"?{parsed.query}")
            if qmatch:
                return qmatch.group(1)

        return None
    except Exception:  # noqa: BLE001
        return None


class MediafireResolver:
    """Resolves Mediafire DDL links via the public file info API."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "mediafire"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a Mediafire link using the file info API.

        Returns a ``ResolvedStream`` with the canonical file URL on success,
        or ``None`` if the file is offline or invalid.
        """
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("mediafire_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(
                _API_URL,
                params={"quick_key": file_id, "response_format": "json"},
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("mediafire_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                "mediafire_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        try:
            data = resp.json()
        except ValueError:
            log.warning("mediafire_invalid_json", url=url)
            return None

        response = data.get("response", {})
        result = response.get("result")

        if result != "Success":
            error = response.get("error")
            if isinstance(error, int) and error in _OFFLINE_ERROR_CODES:
                log.info("mediafire_file_offline", file_id=file_id, error=error)
            else:
                log.warning("mediafire_api_error", file_id=file_id, result=result)
            return None

        file_info = response.get("file_info", {})
        if not isinstance(file_info, dict):
            log.warning("mediafire_missing_file_info", file_id=file_id)
            return None

        # Check if file has been deleted
        if file_info.get("delete_date"):
            log.info("mediafire_file_deleted", file_id=file_id)
            return None

        filename = file_info.get("filename", "")
        log.debug(
            "mediafire_resolved",
            file_id=file_id,
            filename=filename,
        )

        canonical_url = f"https://www.mediafire.com/file/{file_id}"
        return ResolvedStream(video_url=canonical_url, quality=StreamQuality.UNKNOWN)
