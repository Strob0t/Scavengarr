"""How many episodes of long-running anime an IMDb request numbers wrong.

    PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py
        [--kitsu 12,210,...]

Runs in the dev container: it reads two public catalogs, Cinemeta and the
Anime Kitsu addon, and writes nothing. Per series (a Kitsu id; default:
the long-runners below, whose Kitsu entry spans the series, so its episode
number counts as the sites do) the addon's meta lists each Kitsu episode
with how IMDb counts it (``imdbSeason``/``imdbEpisode``), read the way the
app reads it (``kitsu_addon._reduce``). For each mapped episode the probe
asks the app's own episode reference (``TitleResolver.episode_ref``) for
the absolute number an IMDb request for that season and episode gets from
the mapped IMDb id's Cinemeta list (``cinemeta._reduce``) and compares it
with the Kitsu number. Per series it prints the episodes, the mapped ones,
equal, off by +1, by -1, by more, the mapped episodes Cinemeta lacks or
lists in season 0 (no absolute number), the unmapped ones, the regular
Cinemeta entries no Kitsu episode maps to (each one ahead of an episode
puts its number one high) and the first episode that is off (Kitsu
number, IMDb season and episode, offset). Names and counts only.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import SeriesMeta, StremioStreamRequest
from scavengarr.infrastructure.anime import kitsu_addon
from scavengarr.infrastructure.anime.kitsu_addon import ADDON_URL, Placement
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


@dataclass
class Offsets:
    """A series' Kitsu episodes against the absolute numbers IMDb requests
    for their mapped episodes get."""

    name: str
    episodes: int = 0
    mapped: int = 0
    equal: int = 0
    plus_one: int = 0
    minus_one: int = 0
    more: int = 0
    not_listed: int = 0  # Cinemeta lacks the mapped IMDb episode
    season_zero: int = 0  # mapped to a special: the reference has no number
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
) -> Offsets:
    """*episodes* (Kitsu number -> placement) against the absolute numbers
    the app's episode reference gives IMDb requests for their mapped
    episodes in *meta*, Cinemeta's list of *imdb_id*."""
    result = Offsets(name, episodes=len(episodes))
    mapped = {number: p for number, p in episodes.items() if p.mapped}
    result.mapped = len(mapped)
    for number in sorted(mapped):
        placement = mapped[number]
        request = StremioStreamRequest(
            imdb_id, "series", season=placement.season, episode=placement.episode
        )
        ref = TitleResolver.episode_ref(request, meta)
        if ref is None:
            result.not_listed += 1
            continue
        if ref.absolute is None:
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
    "| Series | Kitsu | Episodes | Mapped | Equal | +1 | −1 | Off more"
    " | Not in Cinemeta | Season 0 | Unmapped | Not in Kitsu | First off |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def report_row(kitsu_id: str, offsets: Offsets) -> str:
    first = "–"
    if offsets.first_off is not None:
        number, season, episode, offset = offsets.first_off
        first = f"{number} (S{season}E{episode}, {offset:+d})"
    counts = (
        offsets.episodes,
        offsets.mapped,
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


async def series_offsets(
    http: httpx.AsyncClient, kitsu_id: str
) -> tuple[Offsets | None, str]:
    """The series' offsets and a note: the addon's name when it differs
    from the expected one, or why there are no offsets."""
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
    return compare(name, record.imdb_id, record.episodes, meta), note


async def main(kitsu_ids: list[str]) -> None:
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(headers=headers) as http:
        found = await asyncio.gather(
            *(series_offsets(http, kitsu_id) for kitsu_id in kitsu_ids),
            return_exceptions=True,
        )
    print(_HEADER)
    notes: list[str] = []
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
    return parser


if __name__ == "__main__":
    asyncio.run(main(_parser().parse_args().kitsu))
