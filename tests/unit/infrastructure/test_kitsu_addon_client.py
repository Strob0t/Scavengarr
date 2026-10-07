"""Tests for the Anime Kitsu addon client (``infrastructure/anime/kitsu_addon.py``).

The answers are the addon's own, trimmed to a few videos
(``tests/fixtures/json/kitsu/``).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.infrastructure.anime.kitsu_addon import (
    ADDON_URL,
    AddonRecord,
    KitsuAddonClient,
    Placement,
)

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "json" / "kitsu"
_SERIES = json.loads((_FIXTURES / "meta_series_41982.json").read_text())
_MOVIE = json.loads((_FIXTURES / "meta_movie_11614.json").read_text())
_LONG_RUNNER = json.loads((_FIXTURES / "meta_series_12.json").read_text())


@pytest.fixture()
def cache() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = None
    return mock


@pytest.fixture()
def client(cache: AsyncMock) -> KitsuAddonClient:
    return KitsuAddonClient(http_client=httpx.AsyncClient(), cache=cache)


def _meta_route(kind: str, kitsu_id: str) -> respx.Route:
    return respx.get(f"{ADDON_URL}/meta/{kind}/kitsu:{kitsu_id}.json")


class TestRecords:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_series_record_places_its_episodes_as_imdb_counts(
        self, client: KitsuAddonClient, cache: AsyncMock
    ) -> None:
        _meta_route("series", "41982").respond(json=_SERIES)

        record = await client.fetch("series", "41982")

        assert record == AddonRecord(
            imdb_id="tt2560140",
            content_type="series",
            episodes={
                1: Placement(season=3, episode=13, mapped=True),
                3: Placement(season=3, episode=15, mapped=True),
            },
        )
        cache.set.assert_awaited_once()
        key, value = cache.set.await_args.args
        assert key == "anime_ids:addon:v1:series:41982"
        assert value == {
            "imdb_id": "tt2560140",
            "type": "series",
            "episodes": {"1": [3, 13, True], "3": [3, 15, True]},
        }
        assert cache.set.await_args.kwargs["ttl"] == 30 * 86_400

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_long_runner_places_its_absolute_numbers(
        self, client: KitsuAddonClient
    ) -> None:
        _meta_route("series", "12").respond(json=_LONG_RUNNER)

        record = await client.fetch("series", "12")

        assert record is not None
        assert record.episodes[1000] == Placement(season=21, episode=109, mapped=True)

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_movie_record(self, client: KitsuAddonClient) -> None:
        _meta_route("movie", "11614").respond(json=_MOVIE)

        record = await client.fetch("movie", "11614")

        assert record is not None
        assert record.imdb_id == "tt5311514"
        assert record.content_type == "movie"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_video_without_imdb_numbering_keeps_its_own(
        self, client: KitsuAddonClient
    ) -> None:
        meta = {
            "meta": {
                "type": "series",
                "imdb_id": "tt2560140",
                "videos": [
                    {"season": 1, "episode": 2},
                    {"season": 1, "episode": 4, "imdbSeason": 3, "imdbEpisode": 16},
                    {"title": "no numbers"},
                ],
            }
        }
        _meta_route("series", "41982").respond(json=meta)

        record = await client.fetch("series", "41982")

        assert record is not None
        assert record.episodes == {
            2: Placement(season=1, episode=2, mapped=False),
            4: Placement(season=3, episode=16, mapped=True),
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_record_without_imdb_id(self, client: KitsuAddonClient) -> None:
        _meta_route("series", "48105").respond(json={"meta": {"type": "series"}})

        record = await client.fetch("series", "48105")

        assert record == AddonRecord(imdb_id="", content_type="series", episodes={})


class TestFailures:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_refused_request_gives_nothing_and_caches_nothing(
        self, client: KitsuAddonClient, cache: AsyncMock
    ) -> None:
        _meta_route("series", "41982").respond(status_code=403)

        assert await client.fetch("series", "41982") is None
        cache.set.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_timeout_gives_nothing(self, client: KitsuAddonClient) -> None:
        _meta_route("series", "41982").mock(side_effect=httpx.ReadTimeout)

        assert await client.fetch("series", "41982") is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_answer_without_meta_gives_nothing(
        self, client: KitsuAddonClient
    ) -> None:
        _meta_route("series", "41982").respond(json={"streams": []})

        assert await client.fetch("series", "41982") is None


class TestCache:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_cached_record_asks_the_addon_nothing(
        self, client: KitsuAddonClient, cache: AsyncMock
    ) -> None:
        route = _meta_route("series", "41982").respond(json=_SERIES)
        cache.get.return_value = {
            "imdb_id": "tt2560140",
            "type": "series",
            "episodes": {"3": [3, 15, True]},
        }

        record = await client.cached("series", "41982")

        assert record == AddonRecord(
            imdb_id="tt2560140",
            content_type="series",
            episodes={3: Placement(season=3, episode=15, mapped=True)},
        )
        assert not route.called
        cache.get.assert_awaited_once_with("anime_ids:addon:v1:series:41982")

    @pytest.mark.asyncio
    async def test_without_a_cached_record(
        self, client: KitsuAddonClient, cache: AsyncMock
    ) -> None:
        cache.get.return_value = None

        assert await client.cached("series", "41982") is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_fetch_asks_the_addon_and_refreshes_the_cache(
        self, client: KitsuAddonClient, cache: AsyncMock
    ) -> None:
        route = _meta_route("series", "41982").respond(json=_SERIES)
        cache.get.return_value = {
            "imdb_id": "tt2560140",
            "type": "series",
            "episodes": {},
        }

        record = await client.fetch("series", "41982")

        assert record is not None
        assert 3 in record.episodes
        assert route.called
        cache.get.assert_not_awaited()
        cache.set.assert_awaited_once()
