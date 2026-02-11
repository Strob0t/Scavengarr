"""Stream link repository backed by CachePort (diskcache/redis)."""

from __future__ import annotations

import pickle

import structlog

from scavengarr.domain.entities.stremio import CachedStreamLink
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)


class CacheStreamLinkRepository:
    """Stores cached stream links via CachePort (Redis or Diskcache)."""

    def __init__(self, cache: CachePort, ttl_seconds: int = 7200) -> None:
        self.cache = cache
        self.ttl = ttl_seconds

    async def save(self, link: CachedStreamLink) -> None:
        """Save stream link in cache with TTL."""
        key = f"streamlink:{link.stream_id}"
        await self.cache.set(key, pickle.dumps(link), ttl=self.ttl)
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
            link = pickle.loads(data)  # noqa: S301
            log.debug("stream_link_loaded", stream_id=stream_id)
            return link
        except (pickle.PickleError, TypeError) as e:
            log.error(
                "stream_link_deserialize_error", stream_id=stream_id, error=str(e)
            )
            return None
