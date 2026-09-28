"""fireani.me Python plugin for Scavengarr.

Scrapes fireani.me (German anime streaming site, Nuxt.js SPA) with:
- httpx for all requests
- GET /search?q={query}&page={n} for keyword search: server-rendered, results
  read from the ``__NUXT_DATA__`` payload (30 per page, ``pages`` total)
- Connect RPC (JSON over POST) for everything else:
  - ``api.v1.anime.AnimeService/GetAnime`` {slug} for seasons/episodes
  - ``api.v1.anime.AnimeService/GetEpisode`` {slug, season, episode} for links
- Streaming links filtered to external hosters (skips internal proxy players)
- Category: always 5070 (Anime) since site is anime-only
- Bounded concurrency for episode link fetching

The site gates its player behind a Turnstile check, but only in the browser:
the RPC endpoints answer without a token.
No authentication required.
No working alternative domains (fireanime.to is parked, others don't resolve).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["fireani.me"]
_MAX_PAGES = 34  # 30 results per page -> ~1000 items

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_RPC_PATH = "/api.v1.anime.AnimeService/"
_SEARCH_KEY_PREFIX = "anime-search:/search"

# Internal proxy player names to exclude from results.
_EXCLUDED_PLAYERS = frozenset({"proxyplayerslow", "proxyplayer"})

# Language label mapping for display.
_LANG_LABELS: dict[str, str] = {
    "ger-dub": "German Dub",
    "ger-sub": "German Sub",
    "eng-sub": "English Sub",
}

_NUXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
# devalue wrappers Nuxt uses around a single value
_NUXT_WRAPPERS = frozenset(
    {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "ProtobufMessage"}
)


def _resolve_nuxt(data: list[Any], index: int) -> Any:
    """Resolve entry *index* of a Nuxt (devalue) payload into plain values.

    The payload is a flat list; containers hold indices into it. Lists that
    start with a type tag (``["Reactive", i]``, ``["Set", i, ...]``) wrap
    other entries.
    """
    value = data[index]
    if isinstance(value, dict):
        return {key: _resolve_nuxt(data, i) for key, i in value.items()}
    if not isinstance(value, list):
        return value
    if value and isinstance(value[0], str):
        tag, args = value[0], value[1:]
        if tag in _NUXT_WRAPPERS and args:
            return _resolve_nuxt(data, args[0])
        if tag == "Set":
            return [_resolve_nuxt(data, i) for i in args]
        return None  # EmptyRef, Date, Map, ...: nothing the plugin needs
    return [_resolve_nuxt(data, i) for i in value]


def _parse_search_payload(html: str) -> tuple[list[dict[str, Any]], int]:
    """Return ``(anime entries, page count)`` from a /search page."""
    match = _NUXT_DATA_RE.search(html)
    if match is None:
        return [], 0
    try:
        data = json.loads(match.group(1))
        state = _resolve_nuxt(data, 0)
    except (ValueError, IndexError, TypeError, RecursionError):
        return [], 0

    entries = state.get("data") if isinstance(state, dict) else None
    if not isinstance(entries, dict):
        return [], 0
    for key, entry in entries.items():
        if key.startswith(_SEARCH_KEY_PREFIX) and isinstance(entry, dict):
            items = entry.get("data")
            pages = entry.get("pages")
            return (
                [i for i in items if isinstance(i, dict)]
                if isinstance(items, list)
                else [],
                pages if isinstance(pages, int) else 0,
            )
    return [], 0


def _build_description(anime: dict[str, Any]) -> str:
    """Build a display description from anime search entry data."""
    genres = anime.get("generes", [])
    if not isinstance(genres, list):
        genres = []
    desc = str(anime.get("desc", ""))
    start = anime.get("start")
    end = anime.get("end")

    parts: list[str] = []
    if genres:
        parts.append(", ".join(str(g) for g in genres))
    if start:
        year_str = str(start)
        if end and end != start:
            year_str += f" - {end}"
        parts.append(f"({year_str})")
    if desc:
        if len(desc) > 300:
            desc = desc[:297] + "..."
        parts.append(desc)

    return " ".join(parts) if parts else ""


def _build_metadata(anime: dict[str, Any]) -> dict[str, str]:
    """Build metadata dict from anime search entry data."""
    genres = anime.get("generes", [])
    if not isinstance(genres, list):
        genres = []

    metadata: dict[str, str] = {
        "genres": ", ".join(str(g) for g in genres),
    }
    for key, field in [
        ("rating", "voteAvg"),
        ("votes", "voteCount"),
        ("tmdb", "tmdb"),
        ("imdb", "imdb"),
        ("year", "start"),
    ]:
        val = anime.get(field)
        if val not in (None, "", 0):
            metadata[key] = str(val)

    return metadata


def _build_stream_links(
    episode_links: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Convert API episode links to Scavengarr download_links format.

    Filters out internal proxy players and builds hoster/link/language dicts.
    """
    links: list[dict[str, str]] = []
    for ep_link in episode_links:
        name = str(ep_link.get("name", ""))
        if name.lower().replace(" ", "") in _EXCLUDED_PLAYERS:
            continue

        url = str(ep_link.get("link", ""))
        if not url or not url.startswith("http"):
            continue

        lang = str(ep_link.get("lang", ""))
        lang_label = _LANG_LABELS.get(lang, lang)

        links.append(
            {
                "hoster": name.lower(),
                "link": url,
                "language": lang_label,
            }
        )

    return links


class FireaniPlugin(HttpxPluginBase):
    """Python plugin for fireani.me using httpx (SSR search + Connect RPC)."""

    name = "fireani"
    version = "1.1.0"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self, query: str, page: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch one /search page; returns ``(anime entries, page count)``."""
        html = await self._fetch_text(
            f"{self.base_url}/search",
            params={"q": query, "page": str(page)},
            context="search",
        )
        if html is None:
            return [], 0
        return _parse_search_payload(html)

    async def _api_search(self, query: str) -> list[dict[str, Any]]:
        """Collect search entries across pages (up to ``effective_max_results``)."""
        limit = self.effective_max_results
        items, pages = await self._search_page(query, 1)
        for page in range(2, min(pages, _MAX_PAGES) + 1):
            if len(items) >= limit:
                break
            more, _ = await self._search_page(query, page)
            if not more:
                break
            items.extend(more)

        self._log.info(
            "fireani_search_results",
            query=query,
            results=len(items),
            pages=pages,
        )
        return items[:limit]

    async def _rpc(
        self, method: str, body: dict[str, str], *, context: str
    ) -> dict[str, Any] | None:
        """Call an AnimeService Connect RPC method; returns its ``data``."""
        resp = await self._safe_fetch(
            f"{self.base_url}{_RPC_PATH}{method}",
            method="POST",
            json=body,
            context=context,
        )
        if resp is None:
            return None

        payload = self._safe_parse_json(resp, context=context)
        if not isinstance(payload, dict) or payload.get("status") != 200:
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    async def _get_anime_detail(self, slug: str) -> dict[str, Any] | None:
        """Fetch anime detail (seasons and episode lists) via GetAnime."""
        return await self._rpc("GetAnime", {"slug": slug}, context="detail")

    async def _get_episode_links(
        self,
        slug: str,
        season: str,
        episode: str,
    ) -> list[dict[str, str]]:
        """Fetch streaming links for a specific episode via GetEpisode."""
        ep_data = await self._rpc(
            "GetEpisode",
            {"slug": slug, "season": season, "episode": episode},
            context="episode",
        )
        if ep_data is None:
            return []

        raw_links = ep_data.get("animeEpisodeLinks", [])
        if not isinstance(raw_links, list):
            return []

        return _build_stream_links(raw_links)

    def _find_first_episode(
        self,
        anime_detail: dict[str, Any],
    ) -> tuple[str, str] | None:
        """Find the first valid season/episode from anime detail data.

        Returns (season, episode) tuple or None if no episodes found.
        Prefers numbered seasons over "Filme" (movies).
        """
        seasons = anime_detail.get("animeSeasons", [])
        if not isinstance(seasons, list) or not seasons:
            return None

        # Sort seasons: numbered first, "Filme" last
        numbered: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        for s in seasons:
            if not isinstance(s, dict):
                continue
            season_name = str(s.get("season", ""))
            episodes = s.get("animeEpisodes", [])
            if not isinstance(episodes, list) or not episodes:
                continue
            if season_name.isdigit():
                numbered.append(s)
            else:
                other.append(s)

        # Sort numbered seasons by number
        numbered.sort(key=lambda s: int(str(s.get("season", "0"))))

        ordered = numbered + other
        if not ordered:
            return None

        first_season = ordered[0]
        season_name = str(first_season.get("season", ""))
        episodes = first_season.get("animeEpisodes", [])

        if not episodes:
            return None

        # Sort episodes by episode number
        sorted_eps = sorted(
            episodes,
            key=lambda e: (
                int(str(e.get("episode", "0")))
                if str(e.get("episode", "0")).isdigit()
                else 0
            ),
        )

        first_ep = str(sorted_eps[0].get("episode", "1"))
        return (season_name, first_ep)

    async def _fetch_hoster_links(
        self,
        slug: str,
        detail: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        """Resolve the best episode and return hoster links."""
        if not detail:
            return await self._get_episode_links(slug, "1", "1")
        first_ep = self._find_first_episode(detail)
        if first_ep:
            season, episode = first_ep
            return await self._get_episode_links(slug, season, episode)
        return await self._get_episode_links(slug, "1", "1")

    async def _scrape_anime(
        self,
        anime: dict[str, Any],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Fetch detail + episode links for a single anime result.

        When *season* and *episode* are given, fetches that specific
        episode directly instead of resolving the first available one.
        """
        slug = str(anime.get("slug", ""))
        title = str(anime.get("title", ""))
        if not slug or not title:
            return None

        info = anime
        if season is not None and episode is not None:
            hoster_links = await self._get_episode_links(
                slug, str(season), str(episode)
            )
        else:
            detail = await self._get_anime_detail(slug)
            hoster_links = await self._fetch_hoster_links(slug, detail)
            if detail:
                # GetAnime fills in imdb/tmdb, which search entries lack
                info = {**detail, **anime}

        if not hoster_links:
            self._log.debug("fireani_no_hosters", slug=slug)
            return None

        return SearchResult(
            title=title,
            download_link=hoster_links[0]["link"],
            download_links=hoster_links,
            source_url=f"{self.base_url}/anime/{slug}",
            category=5070,
            description=_build_description(info),
            metadata=_build_metadata(info),
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search fireani.me and return results with streaming links."""
        await self._ensure_client()

        if not query:
            return []

        # Category filter: site is anime-only (5070).
        # Accept parent category 5000 (any TV) as well as exact 5070.
        if not self._category_matches(category, 5070):
            return []

        all_items = await self._api_search(query)
        if not all_items:
            return []

        # Fetch episode links with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(anime: dict[str, Any]) -> SearchResult | None:
            async with sem:
                return await self._scrape_anime(anime, season=season, episode=episode)

        gathered = await asyncio.gather(
            *[_bounded(a) for a in all_items],
            return_exceptions=True,
        )

        return [r for r in gathered if isinstance(r, SearchResult)]


plugin = FireaniPlugin()
