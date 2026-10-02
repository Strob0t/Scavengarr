"""fsst (fsst.online) hoster resolver — MP4 from the Playerjs setup.

kinoger's first player tab links ``fsst.online/embed/<id>/``, which redirects
to the player host (incvideo1.online), a Kernel Video Sharing site. Its
Playerjs setup lists every quality::

    file:"[360p]https://…/get_file/…/992734_360p.mp4/,[720p]…,[1080p]…"

The ``get_file`` links redirect to the MP4 on the CDN and need no headers.
A gone video is a 404 page whose player plays ``video_error.mp4``.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"fsst"})
_FILE_ID_RE = re.compile(r"^/(?:embed|videos)/(\d+)(?:/|$)")
_PLAYER_FILE_RE = re.compile(r"""\bfile:\s*(["'])(.+?)\1""")
_SOURCE_RE = re.compile(r"(?:\[(\d{3,4})p\])?(https?://[^,\s]+)")
_GONE_FILE = "video_error"


def _extract_file_id(url: str) -> str | None:
    """Video id of an fsst embed or video page URL."""
    if extract_domain(url) not in _DOMAINS:
        return None
    match = _FILE_ID_RE.match(urlparse(url).path)
    return match.group(1) if match else None


def _best_source(file: str) -> str | None:
    """Highest quality of a Playerjs ``[720p]<url>,[1080p]<url>`` list."""
    sources = [(int(height or 0), url) for height, url in _SOURCE_RE.findall(file)]
    return max(sources, key=lambda source: source[0])[1] if sources else None


class FsstResolver:
    """Resolves fsst embed pages to the MP4 of their best quality."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "fsst"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Read the embed page's Playerjs sources, pick the best quality."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("fsst_invalid_url", url=url)
            return None

        parsed = urlparse(url)
        embed_url = f"{parsed.scheme}://{parsed.hostname}/embed/{file_id}/"
        try:
            resp = await self._http.get(embed_url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("fsst_request_failed", file_id=file_id)
            return None

        player = _PLAYER_FILE_RE.search(resp.text)
        if resp.status_code == 404 or (player and _GONE_FILE in player.group(2)):
            log.info("fsst_file_offline", file_id=file_id)
            return None
        if resp.status_code != 200:
            log.warning("fsst_http_error", file_id=file_id, status=resp.status_code)
            return None

        source = _best_source(player.group(2)) if player else None
        if not source:
            log.warning("fsst_no_player_source", file_id=file_id)
            return None

        log.debug("fsst_resolved", file_id=file_id, video_url=source[:80])
        return ResolvedStream(
            video_url=source, is_hls=".m3u8" in source, quality=StreamQuality.UNKNOWN
        )
