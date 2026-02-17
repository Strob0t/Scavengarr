"""GoFile hoster resolver — validates DDL links via the GoFile API.

GoFile is a file hosting/sharing service. URLs follow the pattern:
    https://gofile.io/d/{contentId}

Resolution requires an ephemeral guest token obtained via:
    POST https://api.gofile.io/accounts → {"status": "ok", "data": {"token": "..."}}

Content availability is checked via:
    GET https://api.gofile.io/contents/{contentId}
    (with Bearer token auth + Origin header)

Based on JD2 GofileIo.java.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality

log = structlog.get_logger(__name__)

_DOMAINS = {"gofile"}

# Content ID: alphanumeric from /d/ path
_CONTENT_ID_RE = re.compile(r"^/d/([A-Za-z0-9]+)$")

_API_BASE = "https://api.gofile.io"

# Token TTL: 25 minutes (GoFile tokens last ~30 min)
_TOKEN_TTL = 25 * 60

# Module-level token cache
_cached_token: str | None = None
_cached_token_ts: float = 0.0


def _extract_content_id(url: str) -> str | None:
    """Extract the content ID from a GoFile URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if "gofile" not in hostname:
            return None
        match = _CONTENT_ID_RE.search(parsed.path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class GoFileResolver:
    """Resolves GoFile links via the GoFile content API."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "gofile"

    async def _get_guest_token(self) -> str | None:
        """Obtain or reuse a cached guest token."""
        global _cached_token, _cached_token_ts  # noqa: PLW0603

        now = time.monotonic()
        if _cached_token and (now - _cached_token_ts) < _TOKEN_TTL:
            return _cached_token

        try:
            resp = await self._http.post(
                f"{_API_BASE}/accounts",
                json={},
                headers={
                    "Origin": "https://gofile.io",
                    "Referer": "https://gofile.io/",
                },
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("gofile_token_request_failed")
            return None

        if resp.status_code != 200:
            log.warning("gofile_token_http_error", status=resp.status_code)
            return None

        try:
            data = resp.json()
        except ValueError:
            log.warning("gofile_token_invalid_json")
            return None

        if data.get("status") != "ok":
            log.warning("gofile_token_api_error", status=data.get("status"))
            return None

        token = data.get("data", {}).get("token")
        if not token:
            log.warning("gofile_token_missing")
            return None

        _cached_token = token
        _cached_token_ts = now
        log.debug("gofile_token_acquired")
        return token

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a GoFile link by checking the content API.

        Returns a ``ResolvedStream`` with the original URL on success,
        or ``None`` if the content is offline.
        """
        content_id = _extract_content_id(url)
        if not content_id:
            log.warning("gofile_invalid_url", url=url)
            return None

        token = await self._get_guest_token()
        if not token:
            log.warning("gofile_no_token", content_id=content_id)
            return None

        try:
            resp = await self._http.get(
                f"{_API_BASE}/contents/{content_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": "https://gofile.io",
                    "Referer": "https://gofile.io/",
                },
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("gofile_request_failed", content_id=content_id)
            return None

        if resp.status_code == 404:
            log.info("gofile_content_not_found", content_id=content_id)
            return None

        if resp.status_code != 200:
            log.warning(
                "gofile_http_error",
                status=resp.status_code,
                content_id=content_id,
            )
            return None

        try:
            data = resp.json()
        except ValueError:
            log.warning("gofile_invalid_json", content_id=content_id)
            return None

        status = data.get("status")
        if status != "ok":
            log.info("gofile_content_offline", content_id=content_id, status=status)
            return None

        log.debug("gofile_resolved", content_id=content_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)
