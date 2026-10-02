"""FireStream (firestream.to / .site) hoster resolver — signed HLS URL.

Resolution strategy (port of JD2 ``FirestreamTo``):

1. GET the embed page ``/e/{id}`` (firestream.to redirects to
   firestream.site); 404 means the file is gone.
2. ``<script id="video-data">`` JSON: ``video.encodingStatus`` must be
   ``completed`` (otherwise the hoster is still processing the upload).
3. POST ``{"blob": <script id="token-blob">}`` to
   ``/api/videos/{id}/resolve`` on the host that served the page (the token
   is rejected elsewhere) → ``signedVideoUrl``, an HLS playlist.

Playback needs no headers.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"firestream"})
_FILE_ID_RE = re.compile(r"^/e/([A-Za-z0-9]+)(?:/|$)")
_VIDEO_DATA_RE = re.compile(
    r'<script id="video-data" type="application/json">(.*?)</script>', re.DOTALL
)
_TOKEN_RE = re.compile(r'<script id="token-blob" type="text/plain">([^<]*)</script>')


def _extract_file_id(url: str) -> str | None:
    """Extract the video ID from a FireStream embed URL."""
    if extract_domain(url) not in _DOMAINS:
        return None
    match = _FILE_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def _encoding_status(html: str) -> str | None:
    match = _VIDEO_DATA_RE.search(html)
    if not match:
        return None
    try:
        video = json.loads(match.group(1)).get("video") or {}
    except (ValueError, AttributeError):
        return None
    status = video.get("encodingStatus") if isinstance(video, dict) else None
    return status if isinstance(status, str) else None


class FirestreamResolver:
    """Resolves FireStream embed pages to signed HLS playlists."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "firestream"

    @property
    def supported_domains(self) -> frozenset[str]:
        """Mirror domains dispatched to this resolver by the registry."""
        return _DOMAINS

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Read the embed page's token and trade it for the signed stream URL."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("firestream_invalid_url", url=url)
            return None

        parsed = urlparse(url)
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        try:
            page = await self._http.get(
                f"{parsed.scheme or 'https'}://{parsed.hostname}/e/{file_id}",
                headers=headers,
                follow_redirects=True,
                timeout=15,
            )
        except httpx.HTTPError:
            log.warning("firestream_request_failed", url=url)
            return None
        if page.status_code != 200:
            log.info("firestream_offline", file_id=file_id, status=page.status_code)
            return None

        status = _encoding_status(page.text)
        token = _TOKEN_RE.search(page.text)
        if status != "completed" or not token:
            log.info("firestream_not_playable", file_id=file_id, status=status)
            return None

        signed = await self._signed_url(page.url, file_id, token.group(1).strip())
        if not signed:
            return None
        log.debug("firestream_resolved", file_id=file_id, url=signed[:80])
        return ResolvedStream(
            video_url=signed,
            is_hls=".m3u8" in signed,
            quality=StreamQuality.UNKNOWN,
        )

    async def _signed_url(
        self, page_url: httpx.URL, file_id: str, token: str
    ) -> str | None:
        """POST the token to the page's own host (the token is host-bound)."""
        api_url = f"{page_url.scheme}://{page_url.host}/api/videos/{file_id}/resolve"
        try:
            resp = await self._http.post(
                api_url,
                json={"blob": token},
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=15,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning("firestream_resolve_failed", file_id=file_id)
            return None
        signed = data.get("signedVideoUrl") if isinstance(data, dict) else None
        if resp.status_code != 200 or not isinstance(signed, str) or not signed:
            log.warning(
                "firestream_no_stream", file_id=file_id, status=resp.status_code
            )
            return None
        return signed
