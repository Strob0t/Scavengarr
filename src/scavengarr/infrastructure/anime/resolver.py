"""``kitsu:`` requests translated into the ``tt`` requests the pipeline knows,
and IMDb anime episodes numbered as Kitsu counts them.

Order per request: the Anime Kitsu addon's record of the title (cached; asked
again once for an episode newer than the cached record), then the public id
list when the addon does not answer or does not map the entry, then nothing:
the caller answers without streams. The content type comes from the source
(the anime catalogs list everything but movies as series).

An IMDb series request's episode gets its number in the one Kitsu entry that
counts the series from its start, as the anime sites count it: the addon's
records of the list's entries without a season for the IMDb id (cached;
asked again at most hourly when missing or ending before the episode, one
fetch per title that concurrent requests share) count when they name the
IMDb id and place their episode 1 at S1E1, and exactly one of their
episodes must sit at the request's season and episode.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

import structlog

from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.infrastructure.anime.id_lists import AnimeIdLists, ListEntry
from scavengarr.infrastructure.anime.kitsu_addon import AddonRecord, KitsuAddonClient

log = structlog.get_logger(__name__)

# Who placed the episode: IMDb's numbering from the addon, the addon's
# video with Kitsu's own numbering, or the list's season and offset
Source = Literal["addon", "kitsu", "lists"]

# How long an IMDb request's look-up waits before it asks the addon again
# for a title's record that was missing or ended before the episode
_REFETCH_AFTER_S = 3600.0


class KitsuAnimeIdResolver:
    """Implements ``AnimeIdResolverPort`` with the addon and the list."""

    def __init__(self, *, addon: KitsuAddonClient, lists: AnimeIdLists) -> None:
        self._addon = addon
        self._lists = lists
        # When IMDb requests last asked the addon for a title (Kitsu id), and
        # the fetches still running, which concurrent requests share
        self._fetched_at: dict[str, float] = {}
        self._fetching: dict[str, asyncio.Future[AddonRecord | None]] = {}

    async def translate(
        self, request: StremioStreamRequest
    ) -> StremioStreamRequest | None:
        kitsu_id = request.imdb_id.removeprefix("kitsu:")
        episode = request.episode
        if not kitsu_id.isdigit():
            log.warning("anime_id_lookup_failed", kitsu_id=kitsu_id, reason="malformed")
            return None
        record = await self._addon.cached(request.content_type, kitsu_id)
        if record is None or (episode is not None and episode not in record.episodes):
            # Unknown title, or an episode newer than the cached record
            record = await self._addon.fetch(request.content_type, kitsu_id) or record
        translated, source = _from_record(record, episode)
        if translated is None:
            entry = await self._lists.entry(int(kitsu_id))
            translated, source = _from_entry(entry, episode)
        if translated is None:
            log.warning(
                "anime_id_lookup_failed",
                kitsu_id=kitsu_id,
                kitsu_episode=episode,
                reason="unmapped",
                addon_answered=record is not None,
            )
            return None
        log.info(
            "anime_id_translated",
            kitsu_id=kitsu_id,
            kitsu_episode=episode,
            imdb_id=translated.imdb_id,
            content_type=translated.content_type,
            season=translated.season,
            episode=translated.episode,
            source=source,
        )
        return translated

    async def kitsu_episode(self, request: StremioStreamRequest) -> int | None:
        season, episode = request.season, request.episode
        if (
            request.content_type != "series"
            or season is None
            or episode is None
            or not request.imdb_id.startswith("tt")
        ):
            return None
        kitsu_ids = [str(i) for i in self._lists.whole_series(request.imdb_id) or []]
        if not kitsu_ids:
            return None
        place = (season, episode)
        records = await asyncio.gather(
            *(self._series_record(kitsu_id, place) for kitsu_id in kitsu_ids)
        )
        numbers = [
            (kitsu_id, number)
            for kitsu_id, record in zip(kitsu_ids, records, strict=True)
            if record is not None and _counts_from_start(record, request.imdb_id)
            for number, placement in record.episodes.items()
            if placement.mapped and (placement.season, placement.episode) == place
        ]
        if len(numbers) != 1:
            return None
        ((kitsu_id, number),) = numbers
        log.info(
            "anime_kitsu_number",
            imdb_id=request.imdb_id,
            season=season,
            episode=episode,
            kitsu_id=kitsu_id,
            kitsu_episode=number,
        )
        return number

    async def aclose(self) -> None:
        fetches = list(self._fetching.values())
        for fetch in fetches:
            fetch.cancel()
        await asyncio.gather(*fetches, return_exceptions=True)
        await self._lists.aclose()

    async def _series_record(
        self, kitsu_id: str, place: tuple[int, int]
    ) -> AddonRecord | None:
        """The addon's record of *kitsu_id*: the cached one, asked again
        when it is missing or ends before *place* (an episode newer than the
        record), at most once an hour per title (a failed fetch counts).
        Concurrent requests await the one fetch in flight (the addon
        client's one attempt, 5 s timeout)."""
        record = await self._addon.cached("series", kitsu_id)
        if record is not None and not _ends_before(record, place):
            return record
        fetch = self._fetching.get(kitsu_id)
        if fetch is None:
            asked = self._fetched_at.get(kitsu_id)
            if asked is not None and time.monotonic() - asked < _REFETCH_AFTER_S:
                return record
            self._fetched_at[kitsu_id] = time.monotonic()
            fetch = asyncio.ensure_future(self._addon.fetch("series", kitsu_id))
            self._fetching[kitsu_id] = fetch
            fetch.add_done_callback(lambda _: self._fetching.pop(kitsu_id, None))
        # A request cut by its deadline must not cancel the fetch others await
        return await asyncio.shield(fetch) or record


def _counts_from_start(record: AddonRecord, imdb_id: str) -> bool:
    """Whether *record* numbers *imdb_id* from the series' start: its episode
    1 is IMDb's S1E1 (a later series under the same IMDb id counts anew)."""
    first = record.episodes.get(1)
    return (
        record.imdb_id == imdb_id
        and first is not None
        and first.mapped
        and (first.season, first.episode) == (1, 1)
    )


def _ends_before(record: AddonRecord, place: tuple[int, int]) -> bool:
    """Whether *record* places no episode at or after *place* as IMDb counts:
    the episode is newer than the record."""
    mapped = [(p.season, p.episode) for p in record.episodes.values() if p.mapped]
    return not mapped or max(mapped) < place


def _from_record(
    record: AddonRecord | None, episode: int | None
) -> tuple[StremioStreamRequest | None, Source | None]:
    if record is None or not record.imdb_id:
        return None, None
    if record.content_type == "movie":
        return StremioStreamRequest(record.imdb_id, "movie"), "addon"
    if episode is None:
        return StremioStreamRequest(record.imdb_id, "series"), "addon"
    placement = record.episodes.get(episode)
    if placement is None:
        return None, None
    translated = StremioStreamRequest(
        record.imdb_id, "series", season=placement.season, episode=placement.episode
    )
    return translated, "addon" if placement.mapped else "kitsu"


def _from_entry(
    entry: ListEntry | None, episode: int | None
) -> tuple[StremioStreamRequest | None, Source | None]:
    if entry is None:
        return None, None
    if entry.content_type == "movie":
        return StremioStreamRequest(entry.imdb_id, "movie"), "lists"
    if episode is None:
        return StremioStreamRequest(entry.imdb_id, "series"), "lists"
    translated = StremioStreamRequest(
        entry.imdb_id,
        "series",
        season=entry.season or 1,
        episode=episode + entry.episode_offset,
    )
    return translated, "lists"
