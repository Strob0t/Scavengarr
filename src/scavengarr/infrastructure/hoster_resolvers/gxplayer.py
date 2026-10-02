"""gxplayer (watch.gxplayer.xyz) hoster resolver — HLS master from the page.

Resolution strategy (port of JD2 ``GxplayerXyz``):

1. ``GET /watch?v={id}`` (8 characters). The page's video object carries
   ``"id"``, ``"uid"`` and ``"md5"``; a gone video is a 200 page saying
   "Video is not found" (or a 404).
2. The HLS master is ``/m3u8/{uid}/{md5}/master.txt?s=1&id={id}&cache=1``
   on the host that served the page. It ends in ``master.txt`` but is an
   ordinary m3u8.

Playback needs no headers. megakino links it as its "Stream in HD" tab.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)

_DOMAINS = frozenset({"gxplayer"})
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9]{8}$")
_MD5_RE = re.compile(r'"md5":"([a-f0-9]{32})"')
_UID_RE = re.compile(r'"uid":"(\d+)"')
_ID_RE = re.compile(r'"id":"(\d+)"')
_GONE_RE = re.compile(r">\s*Video is not found")


def _extract_video_id(url: str) -> str | None:
    """The ``v`` parameter of a gxplayer watch URL."""
    if extract_domain(url) not in _DOMAINS:
        return None
    parsed = urlparse(url)
    if parsed.path.rstrip("/") != "/watch":
        return None
    video_id = parse_qs(parsed.query).get("v", [""])[0]
    return video_id if _VIDEO_ID_RE.match(video_id) else None


class GxplayerResolver:
    """Resolves gxplayer watch pages to HLS master playlists."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "gxplayer"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Read the video object of the watch page, build the master URL."""
        video_id = _extract_video_id(url)
        if not video_id:
            log.warning("gxplayer_invalid_url", url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("gxplayer_request_failed", video_id=video_id)
            return None

        if resp.status_code == 404 or _GONE_RE.search(resp.text):
            log.info("gxplayer_file_offline", video_id=video_id)
            return None
        if resp.status_code != 200:
            log.warning(
                "gxplayer_http_error", video_id=video_id, status=resp.status_code
            )
            return None

        md5 = _MD5_RE.search(resp.text)
        uid = _UID_RE.search(resp.text)
        number = _ID_RE.search(resp.text)
        if not (md5 and uid and number):
            log.warning("gxplayer_no_video_data", video_id=video_id)
            return None

        master = (
            f"{resp.url.scheme}://{resp.url.host}/m3u8/{uid.group(1)}/"
            f"{md5.group(1)}/master.txt?s=1&id={number.group(1)}&cache=1"
        )
        log.debug("gxplayer_resolved", video_id=video_id, hls_url=master)
        return ResolvedStream(
            video_url=master, is_hls=True, quality=StreamQuality.UNKNOWN
        )
