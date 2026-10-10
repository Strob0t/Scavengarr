"""Search results of Stremio requests, cached with stale-while-revalidate.

Only the title-filtered search results are cached, not resolved streams:
hoster stream URLs expire and some are bound to the resolving IP. An entry
names the plugins whose search had not finished when it was written
(``missing``); a later request completes it once (``title_search.py``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

from scavengarr.domain.entities.stremio import EpisodeRef, StremioStreamRequest
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
    stored_at: float  # time.time() of the search's start
    # The plugins that had not finished when the entry was written: still
    # running, timed out, failed or cancelled. Empty: the entry is complete
    missing: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing

    def __setstate__(self, state: dict[str, Any]) -> None:
        # An entry pickled before ``missing`` existed loads as complete
        object.__setattr__(self, "missing", ())
        self.__dict__.update(state)


def merge(
    entry: CachedSearch, plugin: str, results: list[SearchResult]
) -> CachedSearch:
    """*entry* with *plugin*'s results replaced by *results* (its finished
    run) and its name gone from ``missing``.

    The other plugins' results stay, so a merge never thins an entry: a
    plugin that did not finish keeps its earlier results. ``total`` counts
    the plugin's new matching results in place of its old ones (an entry
    keeps no raw count per plugin).
    """
    kept = [r for r in entry.results if r.metadata.get("source_plugin") != plugin]
    replaced = len(entry.results) - len(kept)
    return CachedSearch(
        results=kept + list(results),
        total=max(entry.total - replaced, 0) + len(results),
        stored_at=entry.stored_at,
        missing=tuple(name for name in entry.missing if name != plugin),
    )


def search_cache_key(
    request: StremioStreamRequest, episode_ref: EpisodeRef | None = None
) -> str:
    """One entry per title, season, episode and placement.

    The content type is part of it: TMDB numbers movies and series
    separately, so ``tmdb:1399`` names a movie and a series. The
    reference's absolute number is part of it (``-`` without one): a
    Kitsu request (its own episode number) and the IMDb request for the
    same season and episode (the list's position) place the episode
    differently on the anime sites, so they neither share an entry nor
    join each other's running search (``title_search.py`` keys its
    single-flight by this key). ``v3``: the entries from before are not
    answered.
    """
    absolute = episode_ref.absolute if episode_ref is not None else None
    return (
        f"stremio:search:v3:{request.content_type}:{request.imdb_id}"
        f":{request.season}:{request.episode}"
        f":{'-' if absolute is None else absolute}"
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
            missing=list(entry.missing),
        )
