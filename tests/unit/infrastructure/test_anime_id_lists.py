"""Tests for the public anime id list (``infrastructure/anime/id_lists.py``).

The list is an excerpt of Fribb's ``anime-list-full.json`` with the spike's
titles (``tests/fixtures/json/kitsu/anime_list_excerpt.json``).
"""

from __future__ import annotations

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
