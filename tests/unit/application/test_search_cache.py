"""Tests for the Stremio search cache (entries, staleness, backend errors)."""

from __future__ import annotations

import pickle
import time
from unittest.mock import AsyncMock

from scavengarr.application.stremio.search_cache import (
    STALE_SECONDS,
    CachedSearch,
    SearchCache,
    merge,
    search_cache_key,
)
from scavengarr.domain.entities.stremio import EpisodeRef, StremioStreamRequest
from scavengarr.domain.plugins.base import SearchResult


def _result(link: str) -> SearchResult:
    return SearchResult(title="Iron Man", download_link=link)


def _of(plugin: str, link: str) -> SearchResult:
    return SearchResult(
        title="Iron Man", download_link=link, metadata={"source_plugin": plugin}
    )


def _entry(*links: str, age: float = 0.0) -> CachedSearch:
    return CachedSearch(
        results=[_result(link) for link in links],
        total=len(links),
        stored_at=time.time() - age,
    )


class TestSearchCacheKey:
    def test_one_key_per_title_season_and_episode(self) -> None:
        movie = StremioStreamRequest(imdb_id="tt0816692", content_type="movie")
        episode = StremioStreamRequest(
            imdb_id="tt0903747", content_type="series", season=1, episode=2
        )

        assert (
            search_cache_key(movie) == "stremio:search:v3:movie:tt0816692:None:None:-"
        )
        assert search_cache_key(episode) == "stremio:search:v3:series:tt0903747:1:2:-"

    def test_the_reference_adds_the_absolute_number(self) -> None:
        """A Kitsu request (its own number) and the IMDb request for the same
        season and episode (the list's position) place the episode
        differently on the anime sites: two entries, two searches."""
        episode = StremioStreamRequest(
            imdb_id="tt0388629", content_type="series", season=22, episode=4
        )
        by_position = EpisodeRef(season=22, episode=4, absolute=1088)
        by_kitsu = EpisodeRef(season=22, episode=4, absolute=1089)
        assert (
            search_cache_key(episode, by_position)
            == "stremio:search:v3:series:tt0388629:22:4:1088"
        )
        assert search_cache_key(episode, by_kitsu) != search_cache_key(
            episode, by_position
        )

    def test_without_a_reference_the_key_is_stable(self) -> None:
        episode = StremioStreamRequest(
            imdb_id="tt0903747", content_type="series", season=1, episode=2
        )
        unnumbered = EpisodeRef(season=1, episode=2, title="Cat's in the Bag...")
        assert search_cache_key(episode) == search_cache_key(episode, None)
        assert search_cache_key(episode, unnumbered) == search_cache_key(episode)

    def test_a_tmdb_movie_and_series_with_one_number_are_two_entries(self) -> None:
        """TMDB numbers movies and series separately: tmdb:1399 is both."""
        movie = StremioStreamRequest(imdb_id="tmdb:1399", content_type="movie")
        series = StremioStreamRequest(imdb_id="tmdb:1399", content_type="series")

        assert search_cache_key(movie) != search_cache_key(series)


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


class TestMissingPlugins:
    """An entry names the plugins that had not finished when it was written
    (continue-cut-searches)."""

    def test_an_entry_from_before_the_field_is_complete(self) -> None:
        entry = object.__new__(CachedSearch)
        entry.__setstate__({"results": [], "total": 1, "stored_at": 1.0})

        assert entry.missing == ()
        assert entry.complete

    def test_missing_survives_pickling(self) -> None:
        entry = CachedSearch(results=[], total=1, stored_at=1.0, missing=("sto",))

        loaded = pickle.loads(pickle.dumps(entry))

        assert loaded.missing == ("sto",)
        assert not loaded.complete

    def test_merge_replaces_the_plugins_results_and_clears_its_name(self) -> None:
        entry = CachedSearch(
            results=[_of("hdfilme", "h1"), _of("sto", "s1"), _of("sto", "s2")],
            total=5,
            stored_at=1.0,
            missing=("kinoger", "sto"),
        )

        merged = merge(entry, "sto", [_of("sto", "s3")])

        assert [r.download_link for r in merged.results] == ["h1", "s3"]
        assert merged.missing == ("kinoger",)
        assert merged.total == 4
        assert merged.stored_at == 1.0

    def test_merge_keeps_the_other_plugins_results(self) -> None:
        """A plugin that finished empty replaces its own results only; the
        others' stay, so no merge thins an entry."""
        entry = CachedSearch(
            results=[_of("hdfilme", "h1"), _of("sto", "s1")],
            total=2,
            stored_at=1.0,
            missing=("kinoger",),
        )

        merged = merge(entry, "kinoger", [])

        assert [r.download_link for r in merged.results] == ["h1", "s1"]
        assert merged.missing == ()
        assert merged.total == 2
