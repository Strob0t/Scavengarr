"""Tests for the Kitsu id resolver (``infrastructure/anime/resolver.py``):
the addon's record first, the public list second, nothing third."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from scavengarr.domain.entities.stremio import StremioContentType, StremioStreamRequest
from scavengarr.infrastructure.anime import kitsu_addon
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


_KITSU = Path(__file__).parents[2] / "fixtures" / "json" / "kitsu"


def _addon_record(name: str) -> AddonRecord:
    """The record the addon client keeps of a captured meta."""
    return kitsu_addon._reduce(json.loads((_KITSU / name).read_text())["meta"])


# Row 72's captures: Duel Monsters (tt0249327, 224 episodes) and an excerpt
# of One Piece (tt0388629: episodes 1 and 1000)
_DUEL = _addon_record("meta_series_442.json")
_ONE_PIECE = _addon_record("meta_series_12.json")


def _numbering(
    whole_series: list[int] | None,
    cached: Mapping[str, AddonRecord],
    fetched: Mapping[str, AddonRecord] | None = None,
) -> tuple[KitsuAnimeIdResolver, AsyncMock, MagicMock]:
    """A resolver whose list names *whole_series* for every IMDb id and whose
    addon has the *cached* records and gives the *fetched* ones."""
    addon = AsyncMock()
    addon.cached.side_effect = lambda _type, kitsu_id: cached.get(kitsu_id)
    addon.fetch.side_effect = lambda _type, kitsu_id: (fetched or {}).get(kitsu_id)
    lists = MagicMock()
    lists.whole_series.return_value = whole_series
    lists.aclose = AsyncMock()
    return KitsuAnimeIdResolver(addon=addon, lists=lists), addon, lists


def _imdb(
    imdb_id: str = "tt0388629",
    season: int | None = 21,
    episode: int | None = 109,
    content_type: StremioContentType = "series",
) -> StremioStreamRequest:
    return StremioStreamRequest(
        imdb_id=imdb_id,
        content_type=content_type,
        season=season,
        episode=episode,
    )


class TestKitsuEpisode:
    """An IMDb series request's episode, numbered as the one Kitsu entry
    that counts the series from its start numbers it."""

    @pytest.mark.asyncio
    async def test_the_kitsu_number_of_an_imdb_episode(self) -> None:
        resolver, addon, lists = _numbering([12], {"12": _ONE_PIECE})

        with structlog.testing.capture_logs() as logs:
            number = await resolver.kitsu_episode(_imdb())

        assert number == 1000
        lists.whole_series.assert_called_once_with("tt0388629")
        addon.cached.assert_awaited_once_with("series", "12")
        addon.fetch.assert_not_awaited()
        (event,) = _events(logs, "anime_kitsu_number")
        assert (event["kitsu_id"], event["kitsu_episode"]) == ("12", 1000)
        assert (event["season"], event["episode"]) == (21, 109)

    @pytest.mark.asyncio
    async def test_later_seasons_get_their_kitsu_numbers(
        self,
    ) -> None:
        resolver, _, _ = _numbering([442], {"442": _DUEL})

        assert await resolver.kitsu_episode(_imdb("tt0249327", 2, 1)) == 50
        assert await resolver.kitsu_episode(_imdb("tt0249327", 4, 1)) == 145

    @pytest.mark.asyncio
    async def test_a_record_of_another_imdb_id_does_not_count(self) -> None:
        """Fribb's list names Duel Monsters tt0247902, the addon tt0249327."""
        resolver, _, _ = _numbering([442], {"442": _DUEL})

        assert await resolver.kitsu_episode(_imdb("tt0247902", 2, 1)) is None

    @pytest.mark.asyncio
    async def test_an_uncached_record_is_fetched(self) -> None:
        resolver, addon, _ = _numbering([12], {}, fetched={"12": _ONE_PIECE})

        assert await resolver.kitsu_episode(_imdb()) == 1000
        addon.fetch.assert_awaited_once_with("series", "12")

    @pytest.mark.asyncio
    async def test_the_addon_down_gives_none_and_is_asked_once_an_hour(
        self,
    ) -> None:
        resolver, addon, _ = _numbering([12], {})

        assert await resolver.kitsu_episode(_imdb()) is None
        assert await resolver.kitsu_episode(_imdb()) is None
        addon.fetch.assert_awaited_once_with("series", "12")

    @pytest.mark.asyncio
    async def test_concurrent_requests_share_one_fetch(self) -> None:
        resolver, addon, _ = _numbering([12], {})
        asked = 0
        both_asked = asyncio.Event()

        async def cached(_type: str, _kitsu_id: str) -> None:
            nonlocal asked
            asked += 1
            if asked == 2:
                both_asked.set()

        async def fetch(_type: str, _kitsu_id: str) -> AddonRecord:
            # Still in flight when the second request looks for the record
            await both_asked.wait()
            return _ONE_PIECE

        addon.cached.side_effect = cached
        addon.fetch.side_effect = fetch

        numbers = await asyncio.gather(
            resolver.kitsu_episode(_imdb()), resolver.kitsu_episode(_imdb())
        )

        assert numbers == [1000, 1000]
        addon.fetch.assert_awaited_once_with("series", "12")
        assert not resolver._fetching  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_a_failed_fetch_leaves_no_fetch_in_flight(self) -> None:
        resolver, addon, _ = _numbering([12], {})

        assert await resolver.kitsu_episode(_imdb()) is None

        assert not resolver._fetching  # noqa: SLF001
        resolver._fetched_at["12"] = time.monotonic() - 3601  # noqa: SLF001
        addon.fetch.side_effect = None
        addon.fetch.return_value = _ONE_PIECE
        assert await resolver.kitsu_episode(_imdb()) == 1000

    @pytest.mark.asyncio
    async def test_an_episode_newer_than_the_record_refetches_once_an_hour(
        self,
    ) -> None:
        newer = AddonRecord(
            imdb_id="tt0388629",
            content_type="series",
            episodes={**_ONE_PIECE.episodes, 1089: Placement(22, 4, mapped=True)},
        )
        resolver, addon, _ = _numbering([12], {"12": _ONE_PIECE}, fetched={"12": newer})

        assert await resolver.kitsu_episode(_imdb(season=22, episode=4)) == 1089
        # Not mapped yet in the addon's newest record: asked again in an hour
        assert await resolver.kitsu_episode(_imdb(season=22, episode=5)) is None
        assert addon.fetch.await_count == 1

        resolver._fetched_at["12"] = time.monotonic() - 3601  # noqa: SLF001
        await resolver.kitsu_episode(_imdb(season=22, episode=5))
        assert addon.fetch.await_count == 2

    @pytest.mark.asyncio
    async def test_an_episode_inside_the_record_asks_no_fetch(self) -> None:
        resolver, addon, _ = _numbering([12], {"12": _ONE_PIECE})

        assert await resolver.kitsu_episode(_imdb(season=10, episode=5)) is None
        addon.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_an_entry_from_the_series_start_counts(self) -> None:
        """Fairy Tail: three season-less entries; only 4676 starts at S1E1."""
        whole = AddonRecord(
            imdb_id="tt1528406",
            content_type="series",
            episodes={
                1: Placement(1, 1, mapped=True),
                175: Placement(5, 25, mapped=True),
            },
        )
        later = AddonRecord(
            imdb_id="tt1528406",
            content_type="series",
            episodes={
                1: Placement(6, 1, mapped=True),
                3: Placement(6, 3, mapped=True),
            },
        )
        resolver, _, _ = _numbering([4676, 8203], {"4676": whole, "8203": later})

        assert await resolver.kitsu_episode(_imdb("tt1528406", 5, 25)) == 175
        assert await resolver.kitsu_episode(_imdb("tt1528406", 6, 3)) is None

    @pytest.mark.asyncio
    async def test_two_numbers_at_one_place_give_none(self) -> None:
        double = AddonRecord(
            imdb_id="tt0388629",
            content_type="series",
            episodes={
                1: Placement(1, 1, mapped=True),
                1000: Placement(21, 109, mapped=True),
                1001: Placement(21, 109, mapped=True),
            },
        )
        resolver, _, _ = _numbering([12], {"12": double})

        assert await resolver.kitsu_episode(_imdb()) is None

    @pytest.mark.asyncio
    async def test_a_list_not_loaded_or_no_anime_asks_no_record(self) -> None:
        for whole_series in (None, []):
            resolver, addon, _ = _numbering(whole_series, {"12": _ONE_PIECE})

            assert await resolver.kitsu_episode(_imdb()) is None
            addon.cached.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "request_",
        [
            _imdb(content_type="movie", season=None, episode=None),
            _imdb(episode=None),
            _imdb(season=None),
            StremioStreamRequest(
                imdb_id="kitsu:12", content_type="series", season=1, episode=1000
            ),
        ],
    )
    async def test_only_an_imdb_series_episode_is_looked_up(
        self, request_: StremioStreamRequest
    ) -> None:
        resolver, _, lists = _numbering([12], {"12": _ONE_PIECE})

        assert await resolver.kitsu_episode(request_) is None
        lists.whole_series.assert_not_called()

    @pytest.mark.asyncio
    async def test_aclose_ends_the_lists_load(self) -> None:
        resolver, _, lists = _numbering(None, {})

        await resolver.aclose()

        lists.aclose.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_aclose_ends_a_fetch_still_running(self) -> None:
        resolver, addon, _ = _numbering([12], {})
        started = asyncio.Event()

        async def never(_type: str, _kitsu_id: str) -> None:
            started.set()
            await asyncio.Event().wait()

        addon.fetch.side_effect = never
        request = asyncio.create_task(resolver.kitsu_episode(_imdb()))
        await started.wait()
        (fetch,) = resolver._fetching.values()  # noqa: SLF001

        await resolver.aclose()

        assert fetch.cancelled()
        (outcome,) = await asyncio.gather(request, return_exceptions=True)
        assert isinstance(outcome, asyncio.CancelledError)


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
