"""fireani.me Python plugin for Scavengarr.

Scrapes fireani.me (German anime streaming site, Nuxt.js SPA with JSON API) with:
- httpx for all requests (pure JSON API, no HTML parsing needed)
- GET /api/anime/search?q={query} for keyword search (max 30 results)
- GET /api/anime?slug={slug} for anime detail (seasons, episodes)
- GET /api/anime/episode?slug={slug}&season={s}&episode={e} for streaming links
- Streaming links filtered to VOE hosters only (skips internal proxy players)
- Category: always 5070 (Anime) since site is anime-only
- Bounded concurrency for episode link fetching

No Cloudflare protection. No authentication required.
No working alternative domains (fireanime.to is parked, others don't resolve).
"""

from __future__ import annotations

import asyncio
from typing import Any

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# Internal proxy player names to exclude from results.
_EXCLUDED_PLAYERS = frozenset({"proxyplayerslow", "proxyplayer"})

# Language label mapping for display.
_LANG_LABELS: dict[str, str] = {
    "ger-dub": "German Dub",
    "ger-sub": "German Sub",
    "eng-sub": "English Sub",
}


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
        ("rating", "vote_avg"),
        ("votes", "vote_count"),
        ("tmdb", "tmdb"),
        ("imdb", "imdb"),
        ("year", "start"),
    ]:
        val = anime.get(field)
        if val is not None:
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
    """Python plugin for fireani.me using httpx (JSON API)."""

    name = "fireani"
    provides = "stream"
    _domains = ["fireani.me"]

    async def _api_search(self, query: str) -> list[dict[str, Any]]:
        """Search via GET /api/anime/search?q={query}.

        Returns list of anime dicts from the API response.
        The API returns max 30 results with no pagination support.
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/api/anime/search",
            context="search",
            params={"q": query},
        )
        if resp is None:
            return []

        data = self._safe_parse_json(resp, context="search")
        if not isinstance(data, dict) or data.get("status") != 200:
            return []

        items = data.get("data", [])
        if not isinstance(items, list):
            return []

        self._log.info(
            "fireani_search_results",
            query=query,
            results=len(items),
        )
        return items

    async def _get_anime_detail(self, slug: str) -> dict[str, Any] | None:
        """Fetch anime detail via GET /api/anime?slug={slug}.

        Returns the anime data dict with seasons and episode lists.
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/api/anime",
            context="detail",
            params={"slug": slug},
        )
        if resp is None:
            return None

        data = self._safe_parse_json(resp, context="detail")
        if not isinstance(data, dict) or data.get("status") != 200:
            return None

        return data.get("data")

    async def _get_episode_links(
        self,
        slug: str,
        season: str,
        episode: str,
    ) -> list[dict[str, str]]:
        """Fetch streaming links for a specific episode.

        Calls GET /api/anime/episode?slug={slug}&season={s}&episode={e}
        and returns filtered hoster links.
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/api/anime/episode",
            context="episode",
            params={"slug": slug, "season": season, "episode": episode},
        )
        if resp is None:
            return []

        data = self._safe_parse_json(resp, context="episode")
        if not isinstance(data, dict) or data.get("status") != 200:
            return []

        ep_data = data.get("data", {})
        if not isinstance(ep_data, dict):
            return []

        raw_links = ep_data.get("anime_episode_links", [])
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
        seasons = anime_detail.get("anime_seasons", [])
        if not isinstance(seasons, list) or not seasons:
            return None

        # Sort seasons: numbered first, "Filme" last
        numbered: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        for s in seasons:
            if not isinstance(s, dict):
                continue
            season_name = str(s.get("season", ""))
            episodes = s.get("anime_episodes", [])
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
        episodes = first_season.get("anime_episodes", [])

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

        if season is not None and episode is not None:
            hoster_links = await self._get_episode_links(
                slug, str(season), str(episode)
            )
        else:
            detail = await self._get_anime_detail(slug)
            hoster_links = await self._fetch_hoster_links(slug, detail)

        if not hoster_links:
            self._log.debug("fireani_no_hosters", slug=slug)
            return None

        description = _build_description(anime)
        metadata = _build_metadata(anime)
        source_url = f"{self.base_url}/anime/{slug}"

        return SearchResult(
            title=title,
            download_link=hoster_links[0]["link"],
            download_links=hoster_links,
            source_url=source_url,
            category=5070,
            description=description,
            metadata=metadata,
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
        # If a non-anime category is requested, return empty.
        if category is not None and category != 5070:
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
