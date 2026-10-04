"""Playmate (playmate.to) hoster resolver — HLS master via the JSON API.

Resolution strategy (port of JD2 ``PlaymateTo``):

1. ``GET /api/video-meta?filecode={id}`` — 404 or ``"success": false`` means
   the file is gone.
2. ``POST /api/s`` with ``{"c": id, "d": "web"}`` (``Origin`` and the watch
   page as ``Referer``) → ``sx``: the HLS master playlist. It ends in
   ``master.txt`` but is an ordinary m3u8.

The API answers 403 to non-browser user agents, so every request sends a
browser User-Agent. Playback needs no headers.
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

_DOMAINS = frozenset({"playmate"})
# Watch page, short and player-frame links (/embed/: the frame some sites link)
_FILE_ID_RE = re.compile(r"^/(?:watch/|e/|embed/)([A-Za-z0-9]{8,})(?:/|$)")
_ORIGIN = "https://playmate.to"


def _extract_file_id(url: str) -> str | None:
    """Extract the file code from a playmate URL."""
    if extract_domain(url) not in _DOMAINS:
        return None
    match = _FILE_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


class PlaymateResolver:
    """Resolves playmate.to watch pages to HLS master playlists."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        return "playmate"

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Check the file via the meta API, then ask the stream API for HLS."""
        file_id = _extract_file_id(url)
        if not file_id:
            log.warning("playmate_invalid_url", url=url)
            return None

        headers = {"User-Agent": DEFAULT_USER_AGENT}
        try:
            meta = await self._http.get(
                f"{_ORIGIN}/api/video-meta",
                params={"filecode": file_id},
                headers=headers,
                timeout=15,
            )
            if meta.status_code == 404 or not meta.json().get("success", True):
                log.info("playmate_file_offline", file_id=file_id)
                return None
            resp = await self._http.post(
                f"{_ORIGIN}/api/s",
                json={"c": file_id, "d": "web"},
                headers={
                    **headers,
                    "Origin": _ORIGIN,
                    "Referer": f"{_ORIGIN}/watch/{file_id}",
                },
                timeout=15,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError, AttributeError):
            log.warning("playmate_request_failed", file_id=file_id)
            return None

        hls = data.get("sx") if isinstance(data, dict) else None
        if resp.status_code != 200 or not isinstance(hls, str) or not hls:
            log.warning("playmate_no_stream", file_id=file_id, status=resp.status_code)
            return None

        log.debug("playmate_resolved", file_id=file_id, hls_url=hls[:80])
        return ResolvedStream(video_url=hls, is_hls=True, quality=StreamQuality.UNKNOWN)
