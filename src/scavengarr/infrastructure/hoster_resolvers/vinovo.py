"""vinovo.to hoster resolver — stream token from the player API.

Resolution strategy (port of JD2 ``VinovoTo``, stream path):

1. GET the embed page ``/e/{file_id}``: page token
   (``<meta name="token" content="<32 hex>">``) and CDN base (``data-base``).
2. POST ``/api/file/url/{file_id}`` (XHR) with ``recaptcha=&token=<page token>``
   → ``{"status": "ok", "token": "<stream token>"}``.
3. Stream URL: ``{data-base}/stream/{stream token}``.

No captcha on this path; Turnstile only guards the official download button
(``/api/file/urldown``). The CDN binds the stream token to the resolving
User-Agent (another UA gets 403) and can take ~30 s to the first byte.
Offline: "Video not found" (no ``data-base`` then).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"vinovo"})
_USER_AGENT = DEFAULT_USER_AGENT
_FILE_ID_RE = re.compile(r"^/(?:e|d)/([a-zA-Z0-9]{12,})(?:/|$)")
_PAGE_TOKEN_RE = re.compile(r'name="token" content="([a-f0-9]{32})"')
_CDN_BASE_RE = re.compile(r'data-base="(https?://[^"]+)"')
_OFFLINE_MARKER = "Video not found"


def _extract_file_id(url: str) -> str | None:
    """Extract the file id from a vinovo ``/e/`` or ``/d/`` URL."""
    try:
        if extract_domain(url) not in _DOMAINS:
            return None
        match = _FILE_ID_RE.match(urlparse(url).path)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


class VinovoResolver:
    """Resolves vinovo.to embeds to a CDN stream URL via the player API."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "vinovo"

    async def resolve(self, url: str) -> ResolvedStream | None:
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("vinovo_invalid_url", url=url)
            return None

        parsed = urlparse(url)
        origin = f"{parsed.scheme or 'https'}://{parsed.hostname or 'vinovo.to'}"
        embed_url = f"{origin}/e/{file_id}"
        try:
            page = await self._http.get(
                embed_url,
                follow_redirects=True,
                timeout=15,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError:
            log.warning("vinovo_request_failed", url=url)
            return None
        if page.status_code != 200:
            log.info("vinovo_http_error", status=page.status_code, url=url)
            return None

        cdn = _CDN_BASE_RE.search(page.text)
        if cdn is None or _OFFLINE_MARKER in page.text:
            log.info("vinovo_offline", file_id=file_id)
            return None
        page_token = _PAGE_TOKEN_RE.search(page.text)
        if page_token is None:
            log.warning("vinovo_no_page_token", file_id=file_id)
            return None

        stream_token = await self._stream_token(
            origin, embed_url, file_id, page_token.group(1)
        )
        if stream_token is None:
            return None

        log.debug("vinovo_resolved", file_id=file_id)
        return ResolvedStream(
            video_url=f"{cdn.group(1).rstrip('/')}/stream/{stream_token}",
            quality=StreamQuality.UNKNOWN,
            headers={"Referer": f"{origin}/", "User-Agent": _USER_AGENT},
        )

    async def _stream_token(
        self, origin: str, embed_url: str, file_id: str, page_token: str
    ) -> str | None:
        try:
            resp = await self._http.post(
                f"{origin}/api/file/url/{file_id}",
                data={"recaptcha": "", "token": page_token},
                timeout=15,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Origin": origin,
                    "Referer": embed_url,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning("vinovo_api_failed", file_id=file_id)
            return None
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get("status") != "ok" or not token:
            message = data.get("message") if isinstance(data, dict) else None
            log.info("vinovo_api_refused", file_id=file_id, message=message)
            return None
        return str(token)
