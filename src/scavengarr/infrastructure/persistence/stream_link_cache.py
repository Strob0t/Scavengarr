"""Stream link repository backed by CachePort (diskcache/redis)."""

from __future__ import annotations

import json
from collections import OrderedDict

import structlog

from scavengarr.domain.entities.stremio import CachedStreamLink
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

# Links of the latest answers kept in memory (one answer saves up to about
# 20; under 1 KB each)
_RECENT_LINKS = 4096


def _serialize_link(link: CachedStreamLink) -> str:
    """Serialize CachedStreamLink to JSON string."""
    return json.dumps(
        {
            "stream_id": link.stream_id,
            "hoster_url": link.hoster_url,
            "title": link.title,
            "hoster": link.hoster,
            "video_url": link.video_url,
            "video_headers": link.video_headers,
            "is_hls": link.is_hls,
            "resolved_at": link.resolved_at,
        }
    )


def _deserialize_link(data: str) -> CachedStreamLink:
    """Deserialize CachedStreamLink from JSON string."""
    d = json.loads(data)
    return CachedStreamLink(
        stream_id=d["stream_id"],
        hoster_url=d["hoster_url"],
        title=d.get("title", ""),
        hoster=d.get("hoster", ""),
        video_url=d.get("video_url", ""),
        video_headers=d.get("video_headers", ""),
        is_hls=d.get("is_hls", False),
        resolved_at=d.get("resolved_at", 0.0),
    )


class CacheStreamLinkRepository:
    """Stores cached stream links via CachePort (Redis or Diskcache).

    The links of the latest answers also stay in memory (``recent``), so
    they play while the cache fails: diskcache raised on every write
    (locked, disk full) and every answer came back empty, Redis lost the
    writes and every ``/play`` answered 404 (code review, 2026-10-06). The
    cache keeps them across restarts.
    """

    def __init__(
        self,
        cache: CachePort,
        ttl_seconds: int = 7 * 24 * 3600,
        *,
        recent: int = _RECENT_LINKS,
    ) -> None:
        self.cache = cache
        self.ttl = ttl_seconds
        self._recent: OrderedDict[str, CachedStreamLink] = OrderedDict()
        self._recent_max = recent

    async def save(self, link: CachedStreamLink) -> None:
        """Save stream link in memory and in the cache with TTL; a failed
        cache write is logged, the link plays from memory."""
        self._recent[link.stream_id] = link
        self._recent.move_to_end(link.stream_id)
        while len(self._recent) > self._recent_max:
            self._recent.popitem(last=False)
        key = f"streamlink:{link.stream_id}"
        try:
            await self.cache.set(key, _serialize_link(link), ttl=self.ttl)
        except Exception:
            log.warning(
                "stream_link_save_failed", stream_id=link.stream_id, exc_info=True
            )
            return
        log.debug(
            "stream_link_saved",
            stream_id=link.stream_id,
            hoster=link.hoster,
            ttl=self.ttl,
        )

    async def get(self, stream_id: str) -> CachedStreamLink | None:
        """Load stream link from memory, else from the cache."""
        recent = self._recent.get(stream_id)
        if recent is not None:
            self._recent.move_to_end(stream_id)
            return recent
        key = f"streamlink:{stream_id}"
        try:
            data = await self.cache.get(key)
        except Exception:
            log.warning("stream_link_load_failed", stream_id=stream_id, exc_info=True)
            return None
        if data is None:
            log.debug("stream_link_not_found", stream_id=stream_id)
            return None

        try:
            link = _deserialize_link(data)
            log.debug("stream_link_loaded", stream_id=stream_id)
            return link
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.error(
                "stream_link_deserialize_error",
                stream_id=stream_id,
                error=str(e),
            )
            return None
