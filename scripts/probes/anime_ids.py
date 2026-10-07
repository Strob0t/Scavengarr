"""Anime ids spike: what a ``kitsu:`` request maps to, and what the sites find.

``probe anime_ids [KITSU_ID[:EPISODE] ...]`` (default: the ten titles of
``docs/plans/anime-ids-spike.md``; no episode: a movie) prints per title:

- Kitsu's entry (subtype, year, romaji and English title, mapping sites, the
  ``thetvdb/season`` id) and its episode record (``seasonNumber``,
  ``relativeNumber``);
- the public id lists: Fribb's anime-lists by Kitsu id (IMDb, TheTVDB, TMDB
  ids, seasons) and Kometa's Anime-IDs by AniDB id (TheTVDB season and
  episode offset, IMDb id);
- the Anime Kitsu addon's meta (the addon that hands out ``kitsu:`` ids):
  the IMDb id, and the episode's ``imdbSeason``/``imdbEpisode``;
- TMDB's ``/find`` by TheTVDB id, when a TMDB key is configured;
- the request the app searches: ``KitsuAnimeIdResolver`` (the addon's record,
  the list as the fallback) run as in production, and the list adapter's
  entry on its own;
- the title the IMDb fallback client gives for the mapped IMDb id;
- how many results aniworld and fireani return for the romaji title and for
  that title, at the addon's season and episode (else the lists').

Read-only (nothing cached, nothing written); prints no URLs or keys.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import StremioStreamRequest
from scavengarr.infrastructure.anime.id_lists import AnimeIdLists
from scavengarr.infrastructure.anime.kitsu_addon import KitsuAddonClient
from scavengarr.infrastructure.anime.resolver import KitsuAnimeIdResolver
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.plugins.registry import PluginRegistry
from scavengarr.infrastructure.tmdb.imdb_fallback import ImdbFallbackClient
from scavengarr.infrastructure.version import APP_USER_AGENT

KITSU = "https://kitsu.io/api/edge"
ADDON = "https://anime-kitsu.strem.fun"
TMDB = "https://api.themoviedb.org/3"
FRIBB = (
    "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
)
KOMETA = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/master/anime_ids.json"
SITES = ("aniworld", "fireani")
KITSU_HEADERS = {"Accept": "application/vnd.api+json"}
# Kitsu id and episode (None: a movie), and why the title is in the spike
TITLES: list[tuple[str, int | None, str]] = [
    ("12", 1000, "one-entry long-runner"),
    ("41982", 3, "split cour (Attack on Titan Season 3 Part 2)"),
    ("49240", 2, "current seasonal series (Frieren 2nd Season)"),
    ("11614", None, "movie (Your Name)"),
    ("43247", 2, "second season, split, with recaps (Re:Zero 2nd Season Part 2)"),
    ("695", 1, "OVA (Hellsing Ultimate)"),
    ("48105", 1, "ONA without TheTVDB mapping (Frieren: Marumaru no Mahou)"),
    ("44081", 2, "aniworld staple (Demon Slayer: Entertainment District Arc)"),
    ("210", 1000, "aniworld staple, long-runner (Detective Conan)"),
    ("48671", 2, "fireani staple (Solo Leveling Season 2)"),
]
# The plugins and clients log at info level
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
)


class MemoryCache:
    """The cache calls the IMDb fallback client makes, kept in memory."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self._data.get(key)

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        del ttl
        self._data[key] = value


@dataclass
class Lookups:
    client: httpx.AsyncClient
    plugins: PluginRegistry
    fallback: ImdbFallbackClient
    resolver: KitsuAnimeIdResolver
    lists: AnimeIdLists
    fribb: dict[int, dict[str, Any]]
    kometa: dict[str, dict[str, Any]]
    tmdb_key: str | None


def translation(translated: StremioStreamRequest | None) -> str:
    if translated is None:
        return "nothing (the request answers no streams)"
    return (
        f"{translated.imdb_id} {translated.content_type}, "
        f"season {translated.season}, episode {translated.episode}"
    )


async def json_of(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """The JSON answer, or None (the error's type only: URLs carry keys)."""
    try:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as error:
        print(f"    ({type(error).__name__})")
        return None


async def kitsu(
    client: httpx.AsyncClient, kitsu_id: str, episode: int | None
) -> dict[str, Any]:
    """Kitsu's entry with its mappings, and the episode's record."""
    entry = await json_of(
        client,
        f"{KITSU}/anime/{kitsu_id}",
        params={"include": "mappings"},
        headers=KITSU_HEADERS,
    )
    if entry is None:
        return {}
    attrs = entry["data"]["attributes"]
    mappings = {
        item["attributes"]["externalSite"]: item["attributes"]["externalId"]
        for item in entry.get("included", [])
        if item["type"] == "mappings"
    }
    record: dict[str, Any] = {}
    if episode is not None:
        episodes = await json_of(
            client,
            f"{KITSU}/anime/{kitsu_id}/episodes",
            params={"filter[number]": episode},
            headers=KITSU_HEADERS,
        )
        for item in (episodes or {}).get("data", []):
            record = item["attributes"]
    titles = attrs.get("titles") or {}
    return {
        "subtype": attrs.get("subtype"),
        "year": (attrs.get("startDate") or "")[:4],
        "romaji": titles.get("en_jp") or attrs.get("canonicalTitle"),
        "english": titles.get("en"),
        "mappings": mappings,
        "season_number": record.get("seasonNumber"),
        "relative_number": record.get("relativeNumber"),
    }


def placement(
    kometa: dict[str, Any] | None, episode: int | None
) -> tuple[int | None, int | None]:
    """Season and episode as TheTVDB counts them, from Kometa's offset."""
    if episode is None:
        return None, None
    if not kometa or kometa.get("tvdb_season") in (None, -1):
        return None, episode  # absolute numbering or unknown
    return kometa["tvdb_season"], episode + (kometa.get("tvdb_epoffset") or 0)


async def addon(
    client: httpx.AsyncClient, kitsu_id: str, episode: int | None, movie: bool
) -> tuple[str | None, int | None, int | None]:
    """The Anime Kitsu addon's IMDb id, season and episode for the entry."""
    kind = "movie" if movie else "series"
    answer = await json_of(client, f"{ADDON}/meta/{kind}/kitsu:{kitsu_id}.json")
    meta = (answer or {}).get("meta") or {}
    for video in meta.get("videos") or []:
        if episode is not None and video.get("episode") == episode:
            return (
                video.get("imdb_id") or meta.get("imdb_id"),
                video.get("imdbSeason"),
                video.get("imdbEpisode"),
            )
    return meta.get("imdb_id"), None, None


async def tmdb_find(client: httpx.AsyncClient, key: str, tvdb_id: int) -> str:
    data = await json_of(
        client,
        f"{TMDB}/find/{tvdb_id}",
        params={"api_key": key, "external_source": "tvdb_id", "language": "de-DE"},
    )
    for kind in ("tv_results", "movie_results"):
        for result in (data or {}).get(kind, []):
            name = result.get("name") or result.get("title")
            return f"{kind.removesuffix('_results')} {result.get('id')} {name!r}"
    return "nothing"


async def hits(
    plugins: PluginRegistry,
    title: str,
    movie: bool,
    season: int | None,
    episode: int | None,
) -> str:
    """Each site's result count and first result for *title*."""
    counts = []
    for name in SITES:
        plugin = plugins.get(name)
        try:
            results = await plugin.search(
                title, 2000 if movie else 5000, season=season, episode=episode
            )
            first = results[0].title if results else ""
            counts.append(f"{name} {len(results)} {first[:40]!r}")
        except Exception as error:  # noqa: BLE001 - a probe reports and goes on
            counts.append(f"{name} {type(error).__name__}")
    return "; ".join(counts)


def print_entry(entry: dict[str, Any], episode: int | None) -> None:
    print(
        f"  kitsu: {entry['subtype']} {entry['year']} {entry['romaji']!r}"
        f" / {entry['english']!r}"
    )
    print(f"  kitsu mappings: {sorted(entry['mappings'])}")
    if "thetvdb/season" in entry["mappings"]:
        print(f"  thetvdb/season id: {entry['mappings']['thetvdb/season']!r}")
    if episode is not None:
        print(
            f"  kitsu episode record: season {entry['season_number']},"
            f" relative {entry['relative_number']}"
        )


def print_lists(listed: dict[str, Any], kometa: dict[str, Any] | None) -> None:
    print(
        f"  fribb: imdb {listed.get('imdb_id')}, tvdb {listed.get('tvdb_id')},"
        f" tmdb {listed.get('themoviedb_id')}, season {listed.get('season')},"
        f" anidb {listed.get('anidb_id')}"
    )
    if kometa:
        print(
            f"  kometa: tvdb season {kometa.get('tvdb_season')},"
            f" offset {kometa.get('tvdb_epoffset')}, imdb {kometa.get('imdb_id')}"
        )
    else:
        print("  kometa: nothing")


async def report(
    lookups: Lookups, kitsu_id: str, episode: int | None, why: str
) -> None:
    """The ids, the placement and the sites' results of one Kitsu title."""
    print(f"\nkitsu:{kitsu_id}" + (f":{episode}" if episode else "") + f"  {why}")
    entry = await kitsu(lookups.client, kitsu_id, episode)
    if not entry:
        return
    print_entry(entry, episode)
    listed = lookups.fribb.get(int(kitsu_id)) or {}
    anidb = entry["mappings"].get("anidb") or listed.get("anidb_id")
    kometa = lookups.kometa.get(str(anidb)) if anidb else None
    print_lists(listed, kometa)
    season, number = placement(kometa, episode)
    print(f"  lists placed: season {season}, episode {number}")
    movie = entry["subtype"] == "movie"
    addon_imdb, addon_season, addon_episode = await addon(
        lookups.client, kitsu_id, episode, movie
    )
    print(f"  addon: imdb {addon_imdb}, season {addon_season}, episode {addon_episode}")
    if addon_season is not None:
        season, number = addon_season, addon_episode
    request = StremioStreamRequest(
        imdb_id=f"kitsu:{kitsu_id}",
        content_type="movie" if movie else "series",
        episode=episode,
    )
    print(f"  resolver: {translation(await lookups.resolver.translate(request))}")
    print(f"  list adapter: {await lookups.lists.entry(int(kitsu_id))}")
    if lookups.tmdb_key and listed.get("tvdb_id"):
        found = await tmdb_find(lookups.client, lookups.tmdb_key, listed["tvdb_id"])
        print(f"  tmdb find: {found}")
    imdb_id = (
        addon_imdb
        or (listed.get("imdb_id") or [None])[0]
        or (kometa or {}).get("imdb_id")
    )
    title = None
    if imdb_id:
        info = await lookups.fallback.get_title_and_year(imdb_id, language="de")
        title = info.title if info else None
        print(f"  imdb fallback ({imdb_id}): {title!r}")
    if season is None and not movie:
        season = 1  # season 0 holds the specials
    print(f"  searched at: season {season}, episode {number}")
    found = await hits(lookups.plugins, entry["romaji"], movie, season, number)
    print(f"  romaji search: {found}")
    if title and title != entry["romaji"]:
        found = await hits(lookups.plugins, title, movie, season, number)
        print(f"  title search: {found}")


async def main(args: list[str]) -> None:
    titles = [
        (kitsu_id, int(episode) if episode else None, "")
        for kitsu_id, _, episode in (arg.partition(":") for arg in args)
    ] or TITLES
    config = load_config()
    plugins = PluginRegistry(config.plugin_dir)
    async with httpx.AsyncClient(
        timeout=60, follow_redirects=True, headers={"User-Agent": APP_USER_AGENT}
    ) as client:
        fribb = await json_of(client, FRIBB) or []
        cache = MemoryCache()
        lists = AnimeIdLists(
            http_client=client,
            cache=cache,  # pyright: ignore[reportArgumentType]
        )
        lookups = Lookups(
            client=client,
            plugins=plugins,
            fallback=ImdbFallbackClient(
                http_client=client,
                cache=cache,  # pyright: ignore[reportArgumentType]
            ),
            resolver=KitsuAnimeIdResolver(
                addon=KitsuAddonClient(
                    http_client=client,
                    cache=cache,  # pyright: ignore[reportArgumentType]
                ),
                lists=lists,
            ),
            lists=lists,
            fribb={e["kitsu_id"]: e for e in fribb if e.get("kitsu_id")},
            kometa=await json_of(client, KOMETA) or {},
            tmdb_key=config.tmdb_api_key,
        )
        print(f"lists: fribb {len(lookups.fribb)}, kometa {len(lookups.kometa)}")
        print(f"tmdb key configured: {bool(config.tmdb_api_key)}")
        for kitsu_id, episode, why in titles:
            await report(lookups, kitsu_id, episode, why)
    for name in SITES:
        cleanup = getattr(plugins.get(name), "cleanup", None)
        if cleanup is not None:
            await cleanup()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
