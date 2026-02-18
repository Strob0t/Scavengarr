"""Stream link repository backed by CachePort (diskcache/redis)."""

from __future__ import annotations

import json

import structlog

from scavengarr.domain.entities.stremio import CachedStreamLink
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)


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
    )


class CacheStreamLinkRepository:
    """Stores cached stream links via CachePort (Redis or Diskcache)."""

    def __init__(self, cache: CachePort, ttl_seconds: int = 7200) -> None:
        self.cache = cache
        self.ttl = ttl_seconds

    async def save(self, link: CachedStreamLink) -> None:
        """Save stream link in cache with TTL."""
        key = f"streamlink:{link.stream_id}"
        await self.cache.set(key, _serialize_link(link), ttl=self.ttl)
        log.debug(
            "stream_link_saved",
            stream_id=link.stream_id,
            hoster=link.hoster,
            ttl=self.ttl,
        )

    async def get(self, stream_id: str) -> CachedStreamLink | None:
        """Load stream link from cache."""
        key = f"streamlink:{stream_id}"
        data = await self.cache.get(key)
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
