"""Tests for the public anime id list (``infrastructure/anime/id_lists.py``).

The list is an excerpt of Fribb's ``anime-list-full.json`` with the spike's
titles (``tests/fixtures/json/kitsu/anime_list_excerpt.json``).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.infrastructure.anime.id_lists import (
    LIST_URL,
    AnimeIdLists,
    ListEntry,
)

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "json" / "kitsu"
_LIST = json.loads((_FIXTURES / "anime_list_excerpt.json").read_text())


@pytest.fixture()
def cache() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = None
    return mock


@pytest.fixture()
def lists(cache: AsyncMock) -> AnimeIdLists:
    return AnimeIdLists(http_client=httpx.AsyncClient(), cache=cache)


async def _loaded(lists: AnimeIdLists) -> None:
    """Wait for the load ``whole_series()`` started in the background."""
    if (task := lists._background) is not None:  # noqa: SLF001
        await task


class TestEntries:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_split_cour_has_its_season_and_offset(
        self, lists: AnimeIdLists
    ) -> None:
        respx.get(LIST_URL).respond(json=_LIST)

        assert await lists.entry(41982) == ListEntry(
            imdb_id="tt2560140", content_type="series", season=3, episode_offset=12
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_movie(self, lists: AnimeIdLists) -> None:
        respx.get(LIST_URL).respond(json=_LIST)

        assert await lists.entry(11614) == ListEntry(
            imdb_id="tt5311514", content_type="movie", season=None, episode_offset=0
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_long_runner_has_no_season(self, lists: AnimeIdLists) -> None:
        respx.get(LIST_URL).respond(json=_LIST)

        assert await lists.entry(12) == ListEntry(
            imdb_id="tt0388629", content_type="series", season=None, episode_offset=0
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_unlisted_id_and_one_without_imdb_id(
        self, lists: AnimeIdLists
    ) -> None:
        records = [*_LIST, {"kitsu_id": 48105, "type": "ONA"}]
        respx.get(LIST_URL).respond(json=records)

        assert await lists.entry(999_999) is None
        assert await lists.entry(48105) is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_the_list_is_downloaded_once_and_cached_a_week(
        self, lists: AnimeIdLists, cache: AsyncMock
    ) -> None:
        route = respx.get(LIST_URL).respond(json=_LIST)

        await lists.entry(41982)
        await lists.entry(12)

        assert route.call_count == 1
        cache.set.assert_awaited_once()
        key, value = cache.set.await_args.args
        assert key == "anime_ids:lists:v1"
        assert value["41982"] == ["tt2560140", "series", 3, 12]
        assert cache.set.await_args.kwargs["ttl"] == 7 * 86_400


class TestCache:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_cached_list_is_not_downloaded(
        self, lists: AnimeIdLists, cache: AsyncMock
    ) -> None:
        route = respx.get(LIST_URL).respond(json=_LIST)
        cache.get.return_value = {"41982": ["tt2560140", "series", 3, 12]}

        entry = await lists.entry(41982)

        assert entry == ListEntry(
            imdb_id="tt2560140", content_type="series", season=3, episode_offset=12
        )
        assert not route.called


class TestFailures:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_failed_download_is_not_retried_within_the_hour(
        self, lists: AnimeIdLists, cache: AsyncMock
    ) -> None:
        route = respx.get(LIST_URL).respond(status_code=500)

        assert await lists.entry(41982) is None
        assert await lists.entry(41982) is None

        assert route.call_count == 1
        cache.set.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_the_download_is_retried_after_an_hour(
        self, lists: AnimeIdLists
    ) -> None:
        route = respx.get(LIST_URL)
        route.side_effect = [httpx.Response(500), httpx.Response(200, json=_LIST)]

        assert await lists.entry(41982) is None
        lists._failed_at = time.monotonic() - 3601  # noqa: SLF001

        assert await lists.entry(41982) is not None
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_timeout_gives_nothing(self, lists: AnimeIdLists) -> None:
        respx.get(LIST_URL).mock(side_effect=httpx.ReadTimeout)

        assert await lists.entry(41982) is None


class TestWholeSeries:
    """The reverse look-up for IMDb requests: per IMDb id the series entries
    without a TVDB season, answered without waiting for the list."""

    # One Piece's specials and movies share its IMDb id in the list
    _RECORDS = [
        *_LIST,
        {
            "kitsu_id": 2361,
            "type": "OVA",
            "imdb_id": ["tt0388629"],
            "season": {"tvdb": 0},
        },
        {"kitsu_id": 5866, "type": "MOVIE", "imdb_id": ["tt0388629"]},
    ]

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_list_not_loaded_answers_none_and_loads_in_the_background(
        self, lists: AnimeIdLists
    ) -> None:
        route = respx.get(LIST_URL).respond(json=self._RECORDS)

        assert lists.whole_series("tt0388629") is None
        await _loaded(lists)

        assert lists.whole_series("tt0388629") == [12]
        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_the_load_can_start_before_the_first_lookup(
        self, lists: AnimeIdLists
    ) -> None:
        """The app's start begins it, so an IMDb request right after a start
        finds the list in memory."""
        route = respx.get(LIST_URL).respond(json=self._RECORDS)

        lists.load_in_background()
        lists.load_in_background()
        await _loaded(lists)

        assert lists.whole_series("tt0388629") == [12]
        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_only_series_entries_without_a_season(
        self, lists: AnimeIdLists
    ) -> None:
        respx.get(LIST_URL).respond(json=self._RECORDS)
        await lists.entry(12)

        assert lists.whole_series("tt0388629") == [12]
        assert lists.whole_series("tt0131179") == [210]
        # Season 3 of Attack on Titan counts that season alone
        assert lists.whole_series("tt2560140") == []
        # An id the list does not name: no anime
        assert lists.whole_series("tt0903747") == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_one_load_at_a_time(self, lists: AnimeIdLists) -> None:
        route = respx.get(LIST_URL).respond(json=self._RECORDS)

        assert lists.whole_series("tt0388629") is None
        assert lists.whole_series("tt0131179") is None
        await _loaded(lists)

        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_failed_load_waits_its_hour(self, lists: AnimeIdLists) -> None:
        route = respx.get(LIST_URL).respond(status_code=500)

        assert lists.whole_series("tt0388629") is None
        await _loaded(lists)
        assert lists.whole_series("tt0388629") is None
        await _loaded(lists)

        assert route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_stale_list_answers_while_it_reloads(
        self, lists: AnimeIdLists
    ) -> None:
        route = respx.get(LIST_URL).respond(json=self._RECORDS)
        await lists.entry(12)
        lists._expires_at = time.monotonic() - 1  # noqa: SLF001

        assert lists.whole_series("tt0388629") == [12]
        await _loaded(lists)

        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_aclose_ends_a_load_still_running(self, cache: AsyncMock) -> None:
        started = asyncio.Event()

        async def never(_key: str) -> None:
            started.set()
            await asyncio.Event().wait()

        cache.get.side_effect = never
        lists = AnimeIdLists(http_client=httpx.AsyncClient(), cache=cache)
        assert lists.whole_series("tt0388629") is None
        await started.wait()
        task = lists._background  # noqa: SLF001

        await lists.aclose()

        assert task is not None
        assert task.cancelled()
