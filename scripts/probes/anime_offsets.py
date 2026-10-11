"""How many episodes of long-running anime an IMDb request numbers wrong.

    PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py
        [--kitsu 12,210,...] [--position]
    PYTHONPATH=src poetry run python -P scripts/probes/anime_offsets.py
        --aniworld [--imdb tt0249327,...] [--slug IMDB=SLUG ...] [--cases 5]

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

``--aniworld`` (row 82) places each regular Cinemeta episode of a series on
aniworld's episode index as the app does: the series the plugin's search
gives Cinemeta's name (the closest relevant hit, as the plugin takes it;
``--slug IMDB=SLUG`` names the series page instead), its season pages read
once through the plugin's own fetcher, the reference ``episode_ref`` builds
with the app's Kitsu number, and ``locate``. Per series (default: the
dub-titled series below and the long-runners by the addon's IMDb id) it
prints the rows, the episodes, the placements at the row with the
reference's number, at a neighbour (with the offsets), by title alone,
nowhere although that row has an English title (N26; in brackets those
whose row is the best title match near the number) and nowhere, the
title-confirmed placements and those at another row than the number's (in
brackets the ties: the number's row matches as well), the share over the
series, and ``--cases`` of each series' N26 cases and placements at another
row. It also reads the aniworld site; nothing is stored.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import (
    EpisodeRef,
    SeriesMeta,
    StremioStreamRequest,
)
from scavengarr.infrastructure.anime import kitsu_addon
from scavengarr.infrastructure.anime.id_lists import AnimeIdLists
from scavengarr.infrastructure.anime.kitsu_addon import (
    ADDON_URL,
    KitsuAddonClient,
    Placement,
)
from scavengarr.infrastructure.anime.resolver import KitsuAnimeIdResolver
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.plugins.dom import parse_page
from scavengarr.infrastructure.plugins.episode_index import (
    _NUMBER_SLACK,
    EpisodeRow,
    _best,
    episode_index,
    locate,
    normalize_title,
)
from scavengarr.infrastructure.plugins.registry import PluginRegistry
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)
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
# Series whose English dub retitled the episodes (N26), by IMDb id with
# Cinemeta's name (checked 2026-10-11); the long-runners join them by the
# addon's IMDb id
DUB_TITLED = {
    "tt0249327": "Yu-Gi-Oh!",
    "tt0168366": "Pokémon",
    "tt0210418": "Digimon Adventure",
    "tt0214341": "Dragon Ball Z",
}
_TIMEOUT_S = 30.0
_REPO = Path(__file__).resolve().parents[2]


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


Placed = Literal["exact", "neighbour", "title", "unconfirmed", "nothing"]


@dataclass(frozen=True)
class Verdict:
    """Where ``locate`` places a reference on a site's index: at the row
    with the reference's number (``exact``), at a row near it the title
    confirms (``neighbour``), by the title alone (``title``), nowhere
    although the row with the number has an English title the reference's
    does not confirm (``unconfirmed``, N26's case) or nowhere (``nothing``),
    with the evidence."""

    kind: Placed
    row: EpisodeRow | None = None  # the row locate gives
    exact: EpisodeRow | None = None  # the row with the reference's number
    offset: int | None = None  # the row's number less the reference's
    confirmed: bool = False  # a title confirmed the row
    score: float | None = None  # the row's title match
    exact_score: float | None = None  # the exact row's title match
    # The best title match within _NUMBER_SLACK of the number
    near: tuple[EpisodeRow, float] | None = None

    @property
    def tie(self) -> bool:
        """Another row taken whose title matches no better than the exact
        row's (``_best`` keeps the first of equal matches)."""
        return (
            self.row != self.exact
            and self.score is not None
            and self.exact_score is not None
            and math.isclose(self.score, self.exact_score)
        )


def _score(row: EpisodeRow | None, title: str) -> float | None:
    """*row*'s title match; ``None`` without a title on either side."""
    scored = _best([row], title) if row is not None and title else None
    return scored[1] if scored is not None else None


def place(index: list[EpisodeRow], ref: EpisodeRef) -> Verdict:
    """*ref* placed on *index* by the app's ``locate``."""
    title = normalize_title(ref.title) if ref.title else ""
    exact: EpisodeRow | None = None
    near: tuple[EpisodeRow, float] | None = None
    if ref.absolute is not None:
        numbered = [
            row
            for row in index
            if row.absolute is not None
            and abs(row.absolute - ref.absolute) <= _NUMBER_SLACK
        ]
        exact = next((row for row in numbered if row.absolute == ref.absolute), None)
        if title:
            near = _best(numbered, title)
    exact_score = _score(exact, title)
    located = locate(index, ref)
    if located is None:
        kind: Placed = "unconfirmed" if exact_score is not None else "nothing"
        return Verdict(kind, exact=exact, exact_score=exact_score, near=near)
    row = located.row
    if located.by == "title":
        kind = "title"
    else:
        kind = "exact" if row == exact else "neighbour"
    offset = None
    if row.absolute is not None and ref.absolute is not None:
        offset = row.absolute - ref.absolute
    return Verdict(
        kind,
        row=row,
        exact=exact,
        offset=offset,
        confirmed=bool(title and row.english),
        score=_score(row, title),
        exact_score=exact_score,
        near=near,
    )


Case = tuple[EpisodeRef, Verdict]


@dataclass
class SiteCounts:
    """A series' regular Cinemeta episodes as ``locate`` places them on the
    site's index of the series under *slug*."""

    name: str
    slug: str
    rows: int = 0
    numbered_rows: int = 0  # rows that show an absolute number
    episodes: int = 0  # regular Cinemeta episodes (season and episode 1 and up)
    kitsu: int = 0  # references with the app's Kitsu number
    kinds: Counter[str] = field(default_factory=Counter)
    offsets: Counter[int] = field(default_factory=Counter)  # the neighbours'
    confirmed: int = 0  # placements a title confirmed, with an exact-number row
    elsewhere: list[Case] = field(default_factory=list)  # ...at another row
    unconfirmed: list[Case] = field(default_factory=list)  # N26's cases

    @property
    def ties(self) -> int:
        """Placements at another row whose title matches no better than the
        exact row's."""
        return sum(1 for _, verdict in self.elsewhere if verdict.tie)

    @property
    def exact_best(self) -> int:
        """N26's cases whose exact row is the best title match near the
        number."""
        return sum(
            1
            for _, verdict in self.unconfirmed
            if verdict.near is not None and verdict.near[0] == verdict.exact
        )


def tally(
    name: str,
    slug: str,
    imdb_id: str,
    index: list[EpisodeRow],
    meta: SeriesMeta,
    numbers: Mapping[tuple[int, int], int] | None = None,
) -> SiteCounts:
    """Each regular episode of *meta*, Cinemeta's list of *imdb_id*, placed
    on *index* by the reference the app builds for an IMDb request, with
    the Kitsu *numbers* its look-up gave (IMDb season and episode -> Kitsu
    number)."""
    counts = SiteCounts(
        name,
        slug,
        rows=len(index),
        numbered_rows=sum(row.absolute is not None for row in index),
    )
    for entry in meta.episodes:
        if entry.season < 1 or entry.episode < 1:
            continue
        counts.episodes += 1
        absolute = (numbers or {}).get((entry.season, entry.episode))
        if absolute is not None:
            counts.kitsu += 1
        request = StremioStreamRequest(
            imdb_id, "series", season=entry.season, episode=entry.episode
        )
        ref = TitleResolver.episode_ref(request, meta, absolute=absolute)
        if ref is None:  # never for a listed entry
            continue
        verdict = place(index, ref)
        counts.kinds[verdict.kind] += 1
        if verdict.kind == "neighbour" and verdict.offset is not None:
            counts.offsets[verdict.offset] += 1
        if verdict.kind == "unconfirmed":
            counts.unconfirmed.append((ref, verdict))
        if verdict.confirmed and verdict.exact is not None:
            counts.confirmed += 1
            if verdict.row != verdict.exact:
                counts.elsewhere.append((ref, verdict))
    return counts


_SITE_HEADER = (
    "| Series | IMDb | aniworld | Rows | Numbered rows | Episodes | Kitsu numbered"
    " | Exact | Neighbour | Title | N26 (exact best) | Nothing | Confirmed"
    " | Elsewhere (ties) |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def site_row(imdb_id: str, counts: SiteCounts) -> str:
    neighbour = str(counts.kinds["neighbour"])
    if counts.offsets:
        spread_ = ", ".join(f"{o:+d}: {n}" for o, n in sorted(counts.offsets.items()))
        neighbour = f"{neighbour} ({spread_})"
    cells = (
        counts.name,
        imdb_id,
        counts.slug,
        counts.rows,
        counts.numbered_rows,
        counts.episodes,
        counts.kitsu,
        counts.kinds["exact"],
        neighbour,
        counts.kinds["title"],
        f"{counts.kinds['unconfirmed']} ({counts.exact_best})",
        counts.kinds["nothing"],
        counts.confirmed,
        f"{len(counts.elsewhere)} ({counts.ties})",
    )
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def share_line(series: Iterable[SiteCounts]) -> str:
    """The share of the title-confirmed placements whose row is not the one
    with the reference's number, over *series*."""
    confirmed = elsewhere = ties = 0
    for counts in series:
        confirmed += counts.confirmed
        elsewhere += len(counts.elsewhere)
        ties += counts.ties
    share = f"{100 * elsewhere / confirmed:.1f} %" if confirmed else "–"
    return (
        f"Title-confirmed placements with an exact-number row: {confirmed},"
        f" at another row: {elsewhere} ({share}), ties {ties}"
    )


def _place_name(row: EpisodeRow) -> str:
    return f"S{row.season}E{row.episode}"


def case_line(ref: EpisodeRef, verdict: Verdict) -> str:
    """A reference, the row it was placed at (or the best match near its
    number), the exact-number row and their title matches."""
    head = f"S{ref.season}E{ref.episode} #{ref.absolute} {ref.title!r}"
    exact = verdict.exact
    exact_part = "no exact row"
    if exact is not None:
        exact_part = (
            f"exact row {_place_name(exact)} {exact.english!r} / {exact.german!r}"
        )
        if verdict.exact_score is not None:
            exact_part += f" {verdict.exact_score:.2f}"
    row = verdict.row
    if row is None:
        near = verdict.near
        tail = f"; best near #{near[0].absolute} {near[1]:.2f}" if near else ""
        return f"{head}: {exact_part}{tail}"
    score = f" {verdict.score:.2f}" if verdict.score is not None else ""
    return (
        f"{head}: {verdict.kind} {_place_name(row)} #{row.absolute}"
        f" {row.english!r} / {row.german!r}{score}; {exact_part}"
    )


def spread(items: list[Any], count: int) -> list[Any]:
    """*count* of *items*, evenly spread (all of them when there are fewer)."""
    if len(items) <= count:
        return list(items)
    return [items[i * len(items) // count] for i in range(count)]


async def site_index(
    plugin: Any, query: str, slug: str | None = None
) -> tuple[str, list[str], list[EpisodeRow]] | None:
    """The series aniworld's search gives *query* (the closest relevant
    hit, as the plugin takes it) or the one under *slug*: its slug, the
    titles of the relevant hits and the rows of its season pages, each
    page read once through the plugin's fetcher; ``None`` without a hit
    or a series page."""
    names = type(plugin)._locate_episode.__globals__
    titles: list[str] = []
    if slug is None:
        hits = relevant_hits(
            await plugin._ajax_search(query),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS,
        )
        if not hits:
            return None
        titles = [hit_title(hit) for hit in hits]
        slug = str(hits[0]["link"]).rstrip("/").rsplit("/", 1)[-1]
    series = f"{plugin.base_url}/anime/stream/{slug}"
    resp = await plugin._safe_fetch(series, context="detail_page")
    if resp is None:
        return None
    detail = await parse_page(names["_DetailPageParser"](plugin.base_url), resp.text)
    rows = await episode_index(
        cache=None,
        key=f"probe:{slug}",
        seasons=detail.seasons or [1],
        season_url=lambda number: f"{series}/staffel-{number}",
        fetch_html=plugin._season_html,
        selectors=names["_ROW_SELECTORS"],
        semaphore=plugin._new_semaphore(),
    )
    return slug, titles, rows


async def aniworld_series(
    http: httpx.AsyncClient,
    plugin: Any,
    resolver: KitsuAnimeIdResolver | None,
    imdb_id: str,
    slug: str | None = None,
) -> tuple[SiteCounts | None, str]:
    """The series' regular episodes placed on aniworld's index (the series
    its search gives Cinemeta's name, or the one under *slug*) and a note:
    the search's relevant hits, or why there are no counts."""
    listed = await _meta(http, f"{CINEMETA_URL}/meta/series/{imdb_id}.json")
    meta = cinemeta._reduce(listed) if listed is not None else None
    if meta is None:
        return None, "Cinemeta knows no such series"
    found = await site_index(plugin, meta.name, slug)
    if found is None:
        return None, f"aniworld lists no series for {meta.name!r}"
    slug, titles, index = found
    numbers = None
    if resolver is not None:
        places = [
            (e.season, e.episode)
            for e in meta.episodes
            if e.season >= 1 and e.episode >= 1
        ]
        numbers = await kitsu_numbers(resolver, imdb_id, places)
    if titles:
        note = f"search {meta.name!r}: " + ", ".join(repr(t) for t in titles)
    else:
        note = f"slug {slug} given"
    return tally(meta.name, slug, imdb_id, index, meta, numbers), note


async def _site_counts(
    http: httpx.AsyncClient,
    plugin: Any,
    resolver: KitsuAnimeIdResolver | None,
    imdb_id: str,
    slug: str | None,
) -> tuple[SiteCounts | None, str]:
    """``aniworld_series``, a failed read named in the note."""
    try:
        return await aniworld_series(http, plugin, resolver, imdb_id, slug)
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"failed ({type(exc).__name__})"


async def site_series(http: httpx.AsyncClient) -> tuple[list[str], list[str]]:
    """The dub-titled series, then the long-runners by the addon's IMDb id,
    each once; and notes on the long-runners without one."""
    imdb_ids = list(DUB_TITLED)
    notes: list[str] = []
    for kitsu_id in LONG_RUNNERS:
        addon = await _meta(http, f"{ADDON_URL}/meta/series/kitsu:{kitsu_id}.json")
        record = kitsu_addon._reduce(addon) if addon is not None else None
        if record is None or not record.imdb_id:
            notes.append(f"kitsu:{kitsu_id}: the addon maps it to no IMDb id")
        elif record.imdb_id not in imdb_ids:
            imdb_ids.append(record.imdb_id)
    return imdb_ids, notes


async def aniworld_main(
    imdb_ids: list[str], slugs: Mapping[str, str], *, cases: int = 5
) -> None:
    config = load_config(cli_overrides={"plugin_dir": _REPO / "plugins"})
    registry = PluginRegistry(config.plugin_dir)
    registry.discover()
    plugin: Any = registry.get("aniworld")
    found: list[tuple[str, SiteCounts]] = []
    notes: list[str] = []
    try:
        await plugin._ensure_client()
        await plugin._verify_domain()
        headers = {"User-Agent": DEFAULT_USER_AGENT}
        async with httpx.AsyncClient(headers=headers) as http:
            resolver = await numbering(http)
            if resolver is None:
                notes.append("the id list did not load: the position alone")
            if not imdb_ids:
                imdb_ids, missing = await site_series(http)
                notes.extend(missing)
            # One series at a time: its season pages under the plugin's bound
            for imdb_id in imdb_ids:
                counts, note = await _site_counts(
                    http, plugin, resolver, imdb_id, slugs.get(imdb_id)
                )
                if counts is not None:
                    found.append((imdb_id, counts))
                notes.append(f"{imdb_id}: {note}")
    finally:
        await plugin.cleanup()
    print("\n".join(site_report(found, cases)))
    for note in notes:
        print(note)


def site_report(found: list[tuple[str, SiteCounts]], cases: int) -> list[str]:
    """The table per series (by IMDb id), the share over them and *cases*
    of each series' N26 cases and placements at another row."""
    lines = [_SITE_HEADER]
    lines.extend(site_row(imdb_id, counts) for imdb_id, counts in found)
    lines.append(share_line(counts for _, counts in found))
    for imdb_id, counts in found:
        for label, listed in (
            ("N26's cases", counts.unconfirmed),
            ("title-confirmed at another row", counts.elsewhere),
        ):
            if listed:
                lines.append(f"\n{counts.name} ({imdb_id}), {label}: {len(listed)}")
                lines.extend(f"  {case_line(*case)}" for case in spread(listed, cases))
    lines.append("")
    return lines


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


def _ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anime_offsets")
    parser.add_argument(
        "--kitsu",
        type=_ids,
        default=list(LONG_RUNNERS),
        help="comma-separated Kitsu ids (default: the long-runners)",
    )
    parser.add_argument(
        "--position",
        action="store_true",
        help="the position alone, without the app's Kitsu number (before row 76)",
    )
    parser.add_argument(
        "--aniworld",
        action="store_true",
        help="place each regular Cinemeta episode on aniworld's index (row 82)",
    )
    parser.add_argument(
        "--imdb",
        type=_ids,
        default=[],
        help="with --aniworld: comma-separated IMDb ids (default: the dub-titled"
        " series and the long-runners)",
    )
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        metavar="IMDB=SLUG",
        help="with --aniworld: the series page of an IMDb id instead of the"
        " search's hit (repeatable)",
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=5,
        help="with --aniworld: the cases shown per series and kind",
    )
    return parser


if __name__ == "__main__":
    # The look-up logs every number it finds, the plugin every request
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    args = _parser().parse_args()
    if args.aniworld:
        slugs = dict(item.split("=", 1) for item in args.slug)
        asyncio.run(aniworld_main(args.imdb, slugs, cases=args.cases))
    else:
        asyncio.run(main(args.kitsu, position=args.position))
