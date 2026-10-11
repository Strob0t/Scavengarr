"""How many episodes of long-running anime an IMDb request numbers wrong.

    PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py
        [--kitsu 12,210,...] [--position]

Runs in the dev container: it reads two public catalogs, Cinemeta and the
Anime Kitsu addon, and Fribb's id list, and writes nothing (the app's
caches stay in memory). Per series (a Kitsu id; default: the long-runners
below, whose Kitsu entry spans the series, so its episode number counts as
the sites do) the addon's meta lists each Kitsu episode with how IMDb
counts it (``imdbSeason``/``imdbEpisode``), read the way the app reads it
(``kitsu_addon._reduce``). For each mapped episode the probe asks the
app's own episode reference (``TitleResolver.episode_ref``) for the
absolute number an IMDb request for that season and episode gets from the
mapped IMDb id's Cinemeta list (``cinemeta._reduce``) and the Kitsu number
the app looks up for it (``KitsuAnimeIdResolver.kitsu_episode``, the id
list and the addon as in production; ``--position``: without it, the
numbering before row 76), and compares it with the Kitsu number. Per
series it prints the episodes, the mapped ones, the ones the look-up
numbered, equal, off by +1, by -1, by more, the mapped episodes Cinemeta
lacks or lists without a number (season 0, episode 0), the unmapped ones,
the regular Cinemeta entries no Kitsu episode maps to (each one ahead of an
episode puts its position one high) and the first episode that is off
(Kitsu number, IMDb season and episode, offset). Names and counts only.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import SeriesMeta, StremioStreamRequest
from scavengarr.infrastructure.anime import kitsu_addon
from scavengarr.infrastructure.anime.id_lists import AnimeIdLists
from scavengarr.infrastructure.anime.kitsu_addon import (
    ADDON_URL,
    KitsuAddonClient,
    Placement,
)
from scavengarr.infrastructure.anime.resolver import KitsuAnimeIdResolver
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.stremio import cinemeta
from scavengarr.infrastructure.stremio.cinemeta import CINEMETA_URL

# Long-runners whose Kitsu entry spans the series, by Kitsu id with the name
# the addon gives (checked against its search catalog, 2026-10-11)
LONG_RUNNERS = {
    "12": "One Piece",
    "210": "Detective Conan",
    "1555": "Naruto: Shippuuden",
    "11": "Naruto",
    "244": "BLEACH",
    "13051": "Boruto: Naruto Next Generations",
    "13209": "Black Clover",
    "4676": "Fairy Tail",
    "6448": "Hunter x Hunter (2011)",
    "10879": "Dragon Ball Super",
    "442": "Yu☆Gi☆Oh! Duel Monsters",
}
_TIMEOUT_S = 30.0


class MemoryCache:
    """The cache calls of the id list and the addon client, kept in memory."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self._data.get(key)

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        del ttl
        self._data[key] = value


@dataclass
class Offsets:
    """A series' Kitsu episodes against the absolute numbers IMDb requests
    for their mapped episodes get."""

    name: str
    episodes: int = 0
    mapped: int = 0
    numbered: int = 0  # the app's look-up gave the IMDb request a Kitsu number
    equal: int = 0
    plus_one: int = 0
    minus_one: int = 0
    more: int = 0
    not_listed: int = 0  # Cinemeta lacks the mapped IMDb episode
    season_zero: int = 0  # a special or an episode 0 entry: no number
    not_in_kitsu: int = 0  # regular Cinemeta entries no Kitsu episode maps to
    # Kitsu number, IMDb season and episode, offset (the app's number less
    # Kitsu's)
    first_off: tuple[int, int, int, int] | None = None

    @property
    def unmapped(self) -> int:
        return self.episodes - self.mapped


def compare(
    name: str,
    imdb_id: str,
    episodes: Mapping[int, Placement],
    meta: SeriesMeta | None,
    numbers: Mapping[tuple[int, int], int] | None = None,
) -> Offsets:
    """*episodes* (Kitsu number -> placement) against the absolute numbers
    the app's episode reference gives IMDb requests for their mapped
    episodes in *meta*, Cinemeta's list of *imdb_id*, with the Kitsu
    *numbers* the app looked up for them (IMDb season and episode -> Kitsu
    number)."""
    result = Offsets(name, episodes=len(episodes))
    mapped = {number: p for number, p in episodes.items() if p.mapped}
    result.mapped = len(mapped)
    listed = {(e.season, e.episode) for e in meta.episodes} if meta else set()
    for number in sorted(mapped):
        placement = mapped[number]
        place = (placement.season, placement.episode)
        if place not in listed:
            result.not_listed += 1
            continue
        absolute = (numbers or {}).get(place)
        if absolute is not None:
            result.numbered += 1
        request = StremioStreamRequest(
            imdb_id, "series", season=placement.season, episode=placement.episode
        )
        ref = TitleResolver.episode_ref(request, meta, absolute=absolute)
        if ref is None or ref.absolute is None:
            result.season_zero += 1
            continue
        offset = ref.absolute - number
        if offset == 0:
            result.equal += 1
            continue
        if offset == 1:
            result.plus_one += 1
        elif offset == -1:
            result.minus_one += 1
        else:
            result.more += 1
        if result.first_off is None:
            result.first_off = (number, placement.season, placement.episode, offset)
    if meta is not None:
        targets = {(p.season, p.episode) for p in mapped.values()}
        result.not_in_kitsu = sum(
            1
            for e in meta.episodes
            if e.season >= 1 and (e.season, e.episode) not in targets
        )
    return result


_HEADER = (
    "| Series | Kitsu | Episodes | Mapped | Numbered | Equal | +1 | −1 | Off more"
    " | Not in Cinemeta | Season 0 | Unmapped | Not in Kitsu | First off |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def report_row(kitsu_id: str, offsets: Offsets) -> str:
    first = "–"
    if offsets.first_off is not None:
        number, season, episode, offset = offsets.first_off
        first = f"{number} (S{season}E{episode}, {offset:+d})"
    counts = (
        offsets.episodes,
        offsets.mapped,
        offsets.numbered,
        offsets.equal,
        offsets.plus_one,
        offsets.minus_one,
        offsets.more,
        offsets.not_listed,
        offsets.season_zero,
        offsets.unmapped,
        offsets.not_in_kitsu,
    )
    cells = " | ".join(str(count) for count in counts)
    return f"| {offsets.name} | {kitsu_id} | {cells} | {first} |"


async def _meta(http: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    resp = await http.get(url, timeout=_TIMEOUT_S, follow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    meta = resp.json().get("meta")
    return meta if isinstance(meta, dict) and meta else None


async def numbering(http: httpx.AsyncClient) -> KitsuAnimeIdResolver | None:
    """The app's Kitsu numbering of IMDb requests, its caches in memory;
    ``None`` when the id list does not load."""
    cache = MemoryCache()
    lists = AnimeIdLists(http_client=http, cache=cache)
    # The look-up never waits for the list: loaded first
    await lists.entry(0)
    if lists.whole_series("") is None:
        return None
    addon = KitsuAddonClient(http_client=http, cache=cache)
    return KitsuAnimeIdResolver(addon=addon, lists=lists)


async def kitsu_numbers(
    resolver: KitsuAnimeIdResolver, imdb_id: str, places: Iterable[tuple[int, int]]
) -> dict[tuple[int, int], int]:
    """The Kitsu number the app gives an IMDb request for each of *places*
    (IMDb season and episode), where it finds one."""
    numbers: dict[tuple[int, int], int] = {}
    for season, episode in places:
        request = StremioStreamRequest(
            imdb_id, "series", season=season, episode=episode
        )
        number = await resolver.kitsu_episode(request)
        if number is not None:
            numbers[(season, episode)] = number
    return numbers


async def series_offsets(
    http: httpx.AsyncClient,
    kitsu_id: str,
    resolver: KitsuAnimeIdResolver | None = None,
) -> tuple[Offsets | None, str]:
    """The series' offsets (with *resolver*'s Kitsu numbers) and a note:
    the addon's name when it differs from the expected one, or why there
    are no offsets."""
    addon = await _meta(http, f"{ADDON_URL}/meta/series/kitsu:{kitsu_id}.json")
    if addon is None:
        return None, "the addon knows no such series"
    name = str(addon.get("name") or "")
    expected = LONG_RUNNERS.get(kitsu_id)
    note = f"the addon names it {name!r}" if expected and name != expected else ""
    record = kitsu_addon._reduce(addon)
    if not record.imdb_id:
        return None, "the addon maps it to no IMDb id"
    listed = await _meta(http, f"{CINEMETA_URL}/meta/series/{record.imdb_id}.json")
    meta = cinemeta._reduce(listed) if listed is not None else None
    if meta is None:
        note = "; ".join(filter(None, (note, "Cinemeta knows no such series")))
    numbers = None
    if resolver is not None:
        places = [(p.season, p.episode) for p in record.episodes.values() if p.mapped]
        numbers = await kitsu_numbers(resolver, record.imdb_id, places)
    return compare(name, record.imdb_id, record.episodes, meta, numbers), note


async def main(kitsu_ids: list[str], *, position: bool = False) -> None:
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    notes: list[str] = []
    async with httpx.AsyncClient(headers=headers) as http:
        resolver = None if position else await numbering(http)
        if not position and resolver is None:
            notes.append("the id list did not load: the position alone")
        found = await asyncio.gather(
            *(series_offsets(http, kitsu_id, resolver) for kitsu_id in kitsu_ids),
            return_exceptions=True,
        )
    print(_HEADER)
    for kitsu_id, outcome in zip(kitsu_ids, found, strict=True):
        if isinstance(outcome, BaseException):
            notes.append(f"kitsu:{kitsu_id}: failed ({type(outcome).__name__})")
            continue
        offsets, note = outcome
        if offsets is not None:
            print(report_row(kitsu_id, offsets))
        if note:
            notes.append(f"kitsu:{kitsu_id}: {note}")
    for note in notes:
        print(note)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anime_offsets")
    parser.add_argument(
        "--kitsu",
        type=lambda value: [part.strip() for part in value.split(",") if part.strip()],
        default=list(LONG_RUNNERS),
        help="comma-separated Kitsu ids (default: the long-runners)",
    )
    parser.add_argument(
        "--position",
        action="store_true",
        help="the position alone, without the app's Kitsu number (before row 76)",
    )
    return parser


if __name__ == "__main__":
    # The look-up logs every number it finds
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    args = _parser().parse_args()
    asyncio.run(main(args.kitsu, position=args.position))
