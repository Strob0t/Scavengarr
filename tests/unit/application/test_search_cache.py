"""Tests for the Stremio search cache (entries, staleness, backend errors)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from scavengarr.application.stremio.search_cache import (
    STALE_SECONDS,
    CachedSearch,
    SearchCache,
    search_cache_key,
)
from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.domain.plugins.base import SearchResult


def _result(link: str) -> SearchResult:
    return SearchResult(title="Iron Man", download_link=link)


def _entry(*links: str, age: float = 0.0) -> CachedSearch:
    return CachedSearch(
        results=[_result(link) for link in links],
        total=len(links),
        stored_at=time.time() - age,
    )


class TestCachedSearch:
    def test_merged_adds_only_new_links(self) -> None:
        entry = _entry("https://a/1")

        merged = entry.merged(
            [_result("https://a/1"), _result("https://b/2"), _result("https://b/2")],
            total=5,
        )

        assert [r.download_link for r in merged.results] == [
            "https://a/1",
            "https://b/2",
        ]
        assert merged.total == 6
        assert merged.stored_at == entry.stored_at


class TestSearchCacheKey:
    def test_one_key_per_title_season_and_episode(self) -> None:
        movie = StremioStreamRequest(imdb_id="tt0816692", content_type="movie")
        episode = StremioStreamRequest(
            imdb_id="tt0903747", content_type="series", season=1, episode=2
        )

        assert search_cache_key(movie) == "stremio:search:tt0816692:None:None"
        assert search_cache_key(episode) == "stremio:search:tt0903747:1:2"


class TestSearchCache:
    async def test_stores_with_ttl_plus_stale_window(self) -> None:
        backend = AsyncMock()
        cache = SearchCache(backend, ttl_seconds=1800)

        await cache.put("k", _entry("https://a/1"))

        backend.set.assert_awaited_once()
        assert backend.set.await_args.kwargs["ttl"] == 1800 + STALE_SECONDS

    async def test_entries_without_results_are_not_stored(self) -> None:
        backend = AsyncMock()

        await SearchCache(backend, ttl_seconds=1800).put("k", _entry())

        backend.set.assert_not_awaited()

    async def test_stale_after_the_ttl(self) -> None:
        cache = SearchCache(AsyncMock(), ttl_seconds=1800)

        assert not cache.is_stale(_entry("https://a/1", age=1799))
        assert cache.is_stale(_entry("https://a/1", age=1801))

    async def test_foreign_values_are_a_miss(self) -> None:
        backend = AsyncMock()
        backend.get = AsyncMock(return_value=["not", "an", "entry"])

        assert await SearchCache(backend, ttl_seconds=1800).get("k") is None

    async def test_backend_errors_are_a_miss(self) -> None:
        backend = AsyncMock()
        backend.get = AsyncMock(side_effect=OSError("disk gone"))
        backend.set = AsyncMock(side_effect=OSError("disk gone"))
        cache = SearchCache(backend, ttl_seconds=1800)

        assert await cache.get("k") is None
        await cache.put("k", _entry("https://a/1"))

    async def test_ttl_zero_turns_it_off(self) -> None:
        backend = AsyncMock()
        cache = SearchCache(backend, ttl_seconds=0)

        assert not cache.enabled
        assert await cache.get("k") is None
        await cache.put("k", _entry("https://a/1"))
        backend.get.assert_not_awaited()
        backend.set.assert_not_awaited()
