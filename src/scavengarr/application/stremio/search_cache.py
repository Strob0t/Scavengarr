"""Search results of Stremio requests, cached with stale-while-revalidate.

Only the title-filtered search results are cached, not resolved streams:
hoster stream URLs expire and some are bound to the resolving IP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

# An entry past its TTL still answers this long while a background search
# refreshes it (stale-while-revalidate, RFC 5861)
STALE_SECONDS = 6 * 3600


@dataclass(frozen=True)
class CachedSearch:
    """The title-filtered search results of one Stremio request."""

    results: list[SearchResult]
    total: int  # results before the title filter
    stored_at: float  # time.time() of the search


def search_cache_key(request: StremioStreamRequest) -> str:
    """One entry per title, season and episode.

    The content type is part of it: TMDB numbers movies and series
    separately, so ``tmdb:1399`` names a movie and a series.
    """
    return (
        f"stremio:search:{request.content_type}:{request.imdb_id}"
        f":{request.season}:{request.episode}"
    )


class SearchCache:
    """Stores ``CachedSearch`` entries in the ``CachePort``; TTL 0 turns it off.

    Errors of the cache backend are logged and never fail a request.
    """

    def __init__(self, cache: CachePort | None, *, ttl_seconds: int) -> None:
        self._cache = cache if ttl_seconds > 0 else None
        self._ttl = ttl_seconds

    @property
    def enabled(self) -> bool:
        return self._cache is not None

    def is_stale(self, entry: CachedSearch) -> bool:
        """Whether *entry* is older than the TTL (still served, but refreshed)."""
        return time.time() - entry.stored_at >= self._ttl

    async def get(self, key: str) -> CachedSearch | None:
        if self._cache is None:
            return None
        try:
            entry = await self._cache.get(key)
        except Exception:
            log.warning("stremio_search_cache_read_error", cache_key=key, exc_info=True)
            return None
        return entry if isinstance(entry, CachedSearch) else None

    async def put(self, key: str, entry: CachedSearch) -> None:
        """Store *entry*; entries without results are not stored."""
        if self._cache is None or not entry.results:
            return
        try:
            await self._cache.set(key, entry, ttl=self._ttl + STALE_SECONDS)
        except Exception:
            log.warning(
                "stremio_search_cache_store_error", cache_key=key, exc_info=True
            )
            return
        log.info(
            "stremio_search_cache_stored",
            cache_key=key,
            result_count=len(entry.results),
        )
