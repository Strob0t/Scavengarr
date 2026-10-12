"""Tests for the Cinemeta client (``infrastructure/stremio/cinemeta.py``).

The answers are Cinemeta's own, trimmed to a few seasons
(``tests/fixtures/json/cinemeta/``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
import structlog

from scavengarr.domain.entities.stremio import EpisodeMeta, SeriesMeta
from scavengarr.infrastructure.stremio.cinemeta import CINEMETA_URL, CinemetaClient

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "json" / "cinemeta"
_ONE_PIECE = json.loads((_FIXTURES / "meta_series_tt0388629.json").read_text())
_DEMON_SLAYER = json.loads((_FIXTURES / "meta_series_tt9335498.json").read_text())
_INCEPTION = json.loads((_FIXTURES / "meta_movie_tt1375666.json").read_text())
_LIVE_URL = "https://cinemeta-live.strem.io"
_LUFFY = "I'm Luffy! The Man Who's Gonna Be King of the Pirates!"
_LABOON = "The First Line of Defense? The Giant Whale Laboon Appears!"


@pytest.fixture()
def cache() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = None
    return mock


@pytest.fixture()
def client(cache: AsyncMock) -> CinemetaClient:
    return CinemetaClient(http_client=httpx.AsyncClient(), cache=cache)


def _route(kind: str, imdb_id: str) -> respx.Route:
    return respx.get(f"{CINEMETA_URL}/meta/{kind}/{imdb_id}.json")


def _events(logs: Sequence[Mapping[str, Any]], event: str) -> list[Mapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


class TestRecords:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_series_meta_lists_its_episodes(
        self, client: CinemetaClient, cache: AsyncMock
    ) -> None:
        _route("series", "tt0388629").respond(json=_ONE_PIECE)

        meta, outcome = await client.lookup("series", "tt0388629")

        assert outcome == "found"
        assert meta is not None
        assert meta.name == "One Piece"
        assert meta.year == 1999  # "1999–"
        assert meta.genres == ("Animation", "Action", "Adventure")
        assert len(meta.episodes) == 68
        assert meta.episodes[2] == EpisodeMeta(
            season=1, episode=1, name=_LUFFY, released="1999-10-20"
        )
        assert EpisodeMeta(5, 2, _LABOON, "2001-03-21") in meta.episodes
        cache.set.assert_awaited_once()
        key, value = cache.set.await_args.args
        assert key == "cinemeta:v1:series:tt0388629"
        assert value["name"] == "One Piece"
        assert value["year"] == 1999
        assert value["genres"] == ["Animation", "Action", "Adventure"]
        assert value["episodes"][2] == [1, 1, _LUFFY, "1999-10-20"]
        assert cache.set.await_args.kwargs["ttl"] == 7 * 86_400

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_movie_meta_has_no_episodes(self, client: CinemetaClient) -> None:
        _route("movie", "tt1375666").respond(json=_INCEPTION)

        meta = await client.meta("movie", "tt1375666")

        assert meta == SeriesMeta(
            name="Inception",
            year=2010,
            genres=("Adventure", "Sci-Fi", "Thriller"),
            episodes=(),
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_ended_series_keeps_its_start_year(
        self, client: CinemetaClient
    ) -> None:
        _route("series", "tt9335498").respond(json=_DEMON_SLAYER)

        meta = await client.meta("series", "tt9335498")

        assert meta is not None
        assert meta.year == 2019  # "2019–2024"
        assert EpisodeMeta(4, 1, "Someone's Dream", "2023-04-09") in meta.episodes

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_video_without_numbers_is_left_out(
        self, client: CinemetaClient
    ) -> None:
        answer = {
            "meta": {
                "name": "Some Show",
                "year": "",
                "videos": [
                    {"season": 1, "episode": 2, "name": "Two"},
                    {"name": "no numbers"},
                    {"season": "1", "episode": 3, "name": "strings"},
                ],
            }
        }
        _route("series", "tt0000001").respond(json=answer)

        meta = await client.meta("series", "tt0000001")

        assert meta == SeriesMeta(
            name="Some Show",
            year=None,
            genres=(),
            episodes=(EpisodeMeta(season=1, episode=2, name="Two", released=None),),
        )


class TestNotFound:
    @respx.mock
    @pytest.mark.asyncio
    async def test_an_unknown_id_is_cached_as_none_for_a_day(
        self, client: CinemetaClient, cache: AsyncMock
    ) -> None:
        # Cinemeta sends an unknown id to its live catalog, which answers 404
        _route("series", "tt9999999999").respond(
            status_code=307,
            headers={"location": f"{_LIVE_URL}/meta/series/tt9999999999.json"},
        )
        respx.get(f"{_LIVE_URL}/meta/series/tt9999999999.json").respond(
            status_code=404, text="Cannot GET /series/tt9999999999.json"
        )

        meta, outcome = await client.lookup("series", "tt9999999999")

        assert (meta, outcome) == (None, "not_found")
        cache.set.assert_awaited_once_with(
            "cinemeta:v1:series:tt9999999999", "none", ttl=86_400
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_answer_without_meta_is_not_found(
        self, client: CinemetaClient, cache: AsyncMock
    ) -> None:
        # A movie id asked as a series: the live catalog answers {}
        _route("series", "tt1375666").respond(json={})

        meta, outcome = await client.lookup("series", "tt1375666")

        assert (meta, outcome) == (None, "not_found")
        cache.set.assert_awaited_once_with(
            "cinemeta:v1:series:tt1375666", "none", ttl=86_400
        )


class TestFailures:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_timeout_logs_the_reason_without_the_url(
        self, client: CinemetaClient, cache: AsyncMock
    ) -> None:
        _route("series", "tt0388629").mock(side_effect=httpx.ReadTimeout)

        with structlog.testing.capture_logs() as logs:
            meta, outcome = await client.lookup("series", "tt0388629")

        assert (meta, outcome) == (None, "error")
        [failed] = _events(logs, "cinemeta_failed")
        assert failed["reason"] == "ReadTimeout"
        assert failed["imdb_id"] == "tt0388629"
        assert "strem.io" not in json.dumps(failed)
        cache.set.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_server_error_logs_its_status(self, client: CinemetaClient) -> None:
        _route("series", "tt0388629").respond(status_code=503)

        with structlog.testing.capture_logs() as logs:
            meta, outcome = await client.lookup("series", "tt0388629")

        assert (meta, outcome) == (None, "error")
        [failed] = _events(logs, "cinemeta_failed")
        assert failed["status"] == 503

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_broken_answer_is_an_error(self, client: CinemetaClient) -> None:
        _route("series", "tt0388629").respond(text="<html>maintenance</html>")

        with structlog.testing.capture_logs() as logs:
            assert await client.meta("series", "tt0388629") is None

        [failed] = _events(logs, "cinemeta_failed")
        assert failed["reason"] == "JSONDecodeError"


class TestCache:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_cached_record_asks_cinemeta_nothing(
        self, client: CinemetaClient, cache: AsyncMock
    ) -> None:
        route = _route("series", "tt0388629").respond(json=_ONE_PIECE)
        cache.get.return_value = {
            "name": "One Piece",
            "year": 1999,
            "genres": ["Animation"],
            "episodes": [[5, 2, _LABOON, "2001-03-21"]],
        }

        meta, outcome = await client.lookup("series", "tt0388629")

        assert outcome == "found"
        assert meta == SeriesMeta(
            name="One Piece",
            year=1999,
            genres=("Animation",),
            episodes=(EpisodeMeta(5, 2, _LABOON, "2001-03-21"),),
        )
        assert not route.called
        cache.get.assert_awaited_once_with("cinemeta:v1:series:tt0388629")
        cache.set.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_cached_none_asks_cinemeta_nothing(
        self, client: CinemetaClient, cache: AsyncMock
    ) -> None:
        route = _route("series", "tt9999999999").respond(json=_ONE_PIECE)
        cache.get.return_value = "none"

        assert await client.lookup("series", "tt9999999999") == (None, "not_found")
        assert not route.called
