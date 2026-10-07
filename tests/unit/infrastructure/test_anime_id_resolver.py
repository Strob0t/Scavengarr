"""Tests for the Kitsu id resolver (``infrastructure/anime/resolver.py``):
the addon's record first, the public list second, nothing third."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.infrastructure.anime.id_lists import ListEntry
from scavengarr.infrastructure.anime.kitsu_addon import AddonRecord, Placement
from scavengarr.infrastructure.anime.resolver import KitsuAnimeIdResolver

_RECORD = AddonRecord(
    imdb_id="tt2560140",
    content_type="series",
    episodes={
        1: Placement(season=3, episode=13, mapped=True),
        3: Placement(season=3, episode=15, mapped=True),
    },
)
_ENTRY = ListEntry(
    imdb_id="tt2560140", content_type="series", season=3, episode_offset=12
)


def _resolver(
    *,
    cached: AddonRecord | None = None,
    fetched: AddonRecord | None = None,
    entry: ListEntry | None = None,
) -> tuple[KitsuAnimeIdResolver, AsyncMock, AsyncMock]:
    addon = AsyncMock()
    addon.cached.return_value = cached
    addon.fetch.return_value = fetched
    lists = AsyncMock()
    lists.entry.return_value = entry
    return KitsuAnimeIdResolver(addon=addon, lists=lists), addon, lists


def _request(raw: str = "kitsu:41982", episode: int | None = 3) -> StremioStreamRequest:
    return StremioStreamRequest(imdb_id=raw, content_type="series", episode=episode)


def _events(logs: Sequence[Mapping[str, Any]], event: str) -> list[Mapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == event]


class TestAddonRecord:
    @pytest.mark.asyncio
    async def test_a_cached_record_places_the_episode_without_a_fetch(self) -> None:
        resolver, addon, lists = _resolver(cached=_RECORD)

        with structlog.testing.capture_logs() as logs:
            translated = await resolver.translate(_request())

        assert translated == StremioStreamRequest(
            imdb_id="tt2560140", content_type="series", season=3, episode=15
        )
        addon.fetch.assert_not_awaited()
        lists.entry.assert_not_awaited()
        (event,) = _events(logs, "anime_id_translated")
        assert event["source"] == "addon"
        assert event["kitsu_id"] == "41982"
        assert event["kitsu_episode"] == 3

    @pytest.mark.asyncio
    async def test_without_a_cached_record_the_addon_is_asked(self) -> None:
        resolver, addon, _ = _resolver(fetched=_RECORD)

        translated = await resolver.translate(_request())

        assert translated is not None
        assert (translated.season, translated.episode) == (3, 15)
        addon.fetch.assert_awaited_once_with("series", "41982")

    @pytest.mark.asyncio
    async def test_an_episode_newer_than_the_cached_record_refetches_once(
        self,
    ) -> None:
        stale = AddonRecord(imdb_id="tt2560140", content_type="series", episodes={})
        resolver, addon, _ = _resolver(cached=stale, fetched=_RECORD)

        translated = await resolver.translate(_request())

        assert translated is not None
        assert (translated.season, translated.episode) == (3, 15)
        addon.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_video_without_imdb_numbering_keeps_kitsus(self) -> None:
        record = AddonRecord(
            imdb_id="tt2560140",
            content_type="series",
            episodes={3: Placement(season=1, episode=3, mapped=False)},
        )
        resolver, _, _ = _resolver(cached=record)

        with structlog.testing.capture_logs() as logs:
            translated = await resolver.translate(_request())

        assert translated is not None
        assert (translated.season, translated.episode) == (1, 3)
        assert _events(logs, "anime_id_translated")[0]["source"] == "kitsu"

    @pytest.mark.asyncio
    async def test_a_movie_whatever_the_route_said(self) -> None:
        record = AddonRecord(imdb_id="tt5311514", content_type="movie", episodes={})
        resolver, _, _ = _resolver(cached=record)

        translated = await resolver.translate(_request("kitsu:11614", episode=1))

        assert translated == StremioStreamRequest(
            imdb_id="tt5311514", content_type="movie"
        )

    @pytest.mark.asyncio
    async def test_a_series_without_an_episode(self) -> None:
        resolver, _, _ = _resolver(cached=_RECORD)

        translated = await resolver.translate(_request(episode=None))

        assert translated == StremioStreamRequest(
            imdb_id="tt2560140", content_type="series"
        )


class TestListFallback:
    @pytest.mark.asyncio
    async def test_the_addon_down_the_list_places_the_episode(self) -> None:
        resolver, _, lists = _resolver(entry=_ENTRY)

        with structlog.testing.capture_logs() as logs:
            translated = await resolver.translate(_request())

        assert translated == StremioStreamRequest(
            imdb_id="tt2560140", content_type="series", season=3, episode=15
        )
        lists.entry.assert_awaited_once_with(41982)
        assert _events(logs, "anime_id_translated")[0]["source"] == "lists"

    @pytest.mark.asyncio
    async def test_an_episode_unknown_to_the_fresh_record_asks_the_list(
        self,
    ) -> None:
        resolver, _, lists = _resolver(cached=_RECORD, fetched=_RECORD, entry=_ENTRY)

        translated = await resolver.translate(_request(episode=5))

        assert translated is not None
        assert (translated.season, translated.episode) == (3, 17)
        lists.entry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_record_without_imdb_id_asks_the_list(self) -> None:
        unmapped = AddonRecord(imdb_id="", content_type="series", episodes={})
        resolver, _, _ = _resolver(cached=unmapped, entry=_ENTRY)

        translated = await resolver.translate(_request())

        assert translated is not None
        assert translated.imdb_id == "tt2560140"

    @pytest.mark.asyncio
    async def test_an_entry_without_a_season_is_season_one(self) -> None:
        entry = ListEntry(
            imdb_id="tt0388629", content_type="series", season=None, episode_offset=0
        )
        resolver, _, _ = _resolver(entry=entry)

        translated = await resolver.translate(_request("kitsu:12", episode=1000))

        assert translated == StremioStreamRequest(
            imdb_id="tt0388629", content_type="series", season=1, episode=1000
        )

    @pytest.mark.asyncio
    async def test_a_listed_movie(self) -> None:
        entry = ListEntry(
            imdb_id="tt5311514", content_type="movie", season=None, episode_offset=0
        )
        resolver, _, _ = _resolver(entry=entry)

        translated = await resolver.translate(_request("kitsu:11614", episode=1))

        assert translated == StremioStreamRequest(
            imdb_id="tt5311514", content_type="movie"
        )


class TestNothingMaps:
    @pytest.mark.asyncio
    async def test_neither_source_gives_none_and_logs(self) -> None:
        resolver, _, _ = _resolver()

        with structlog.testing.capture_logs() as logs:
            assert await resolver.translate(_request()) is None

        (event,) = _events(logs, "anime_id_lookup_failed")
        assert event["kitsu_id"] == "41982"
        assert event["reason"] == "unmapped"
        assert event["addon_answered"] is False

    @pytest.mark.asyncio
    async def test_a_malformed_id_asks_nothing(self) -> None:
        resolver, addon, lists = _resolver()

        assert await resolver.translate(_request("kitsu:abc")) is None
        addon.cached.assert_not_awaited()
        lists.entry.assert_not_awaited()
