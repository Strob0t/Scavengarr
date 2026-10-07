"""``kitsu:`` requests translated into the ``tt`` requests the pipeline knows.

Order per request: the Anime Kitsu addon's record of the title (cached; asked
again once for an episode newer than the cached record), then the public id
list when the addon does not answer or does not map the entry, then nothing:
the caller answers without streams. The content type comes from the source
(the anime catalogs list everything but movies as series).
"""

from __future__ import annotations

from typing import Literal

import structlog

from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.infrastructure.anime.id_lists import AnimeIdLists, ListEntry
from scavengarr.infrastructure.anime.kitsu_addon import AddonRecord, KitsuAddonClient

log = structlog.get_logger(__name__)

# Who placed the episode: IMDb's numbering from the addon, the addon's
# video with Kitsu's own numbering, or the list's season and offset
Source = Literal["addon", "kitsu", "lists"]


class KitsuAnimeIdResolver:
    """Implements ``AnimeIdResolverPort`` with the addon and the list."""

    def __init__(self, *, addon: KitsuAddonClient, lists: AnimeIdLists) -> None:
        self._addon = addon
        self._lists = lists

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
