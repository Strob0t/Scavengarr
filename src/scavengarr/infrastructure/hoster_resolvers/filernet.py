"""Filer.net hoster resolver — validates DDL links via the public status API.

Filer.net is a German file hosting service. URLs follow the pattern:
    https://filer.net/get/{hash}
    https://filer.net/dl/{hash}

The public status API (no auth required) returns file metadata:
    GET https://filer.net/api/status/{hash}.json
    → {code: 200, status: "success", data: {file_hash, file_name, file_size, ...}}

Based on JD2 FilerNet.java.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

# URL pattern: /get/{hash} or /dl/{hash}, hash is lowercase alphanumeric
_HASH_RE = re.compile(r"/(?:get|dl)/([a-z0-9]+)")

_API_BASE = "https://filer.net/api"


def _extract_hash(url: str) -> str | None:
    """Extract the file hash from a filer.net URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if "filer.net" not in hostname:
            return None
        match = _HASH_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class FilerNetResolver:
    """Resolves filer.net DDL links by validating via the public status API."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "filer"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a filer.net link and return file metadata.

        Uses the public status API to check if the file exists.
        Returns a ResolvedStream with the canonical download URL.
        """
        file_hash = _extract_hash(url)
        if not file_hash:
            log.warning("filernet_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(
                f"{_API_BASE}/status/{file_hash}.json",
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("filernet_request_failed", url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                "filernet_http_error",
                status=resp.status_code,
                url=url,
            )
            return None

        try:
            data = resp.json()
        except ValueError:
            log.warning("filernet_invalid_json", url=url)
            return None

        # API wraps payload in {code, status, data}
        file_data = data.get("data", {})
        if not isinstance(file_data, dict):
            file_data = {}

        # Verify the hash matches (API returns file_hash in data)
        returned_hash = file_data.get("file_hash", "")
        if returned_hash != file_hash:
            log.info("filernet_file_not_found", file_hash=file_hash)
            return None

        file_name = file_data.get("file_name", "")
        file_size = file_data.get("file_size", 0)

        log.debug(
            "filernet_resolved",
            file_hash=file_hash,
            file_name=file_name,
            file_size=file_size,
        )

        # Return canonical download URL
        download_url = f"https://filer.net/get/{file_hash}"
        return ResolvedStream(
            video_url=download_url,
            quality=StreamQuality.UNKNOWN,
        )
