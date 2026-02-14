"""cineby Python plugin for Scavengarr.

Scrapes Cineby (streaming site) via its public JSON API at db.videasy.net:
- GET /3/search/multi for multi-search (movies + TV, 20 results/page)
- GET /3/search/movie for movie-only search
- GET /3/search/tv for TV-only search
- GET /3/movie/{id} for movie detail (includes IMDB ID, runtime)
- GET /3/tv/{id} for TV detail (includes IMDB ID, episode_run_time)

Streaming via vidking.net embed player:
- Movies: https://www.vidking.net/embed/movie/{tmdb_id}
- TV: https://www.vidking.net/embed/tv/{tmdb_id}/{season}/{episode}

Movies and TV shows. No authentication required.
Reachable alternative domains: cineby.gd, cineby.app, cineby.xyz,
cineby.today, cineby.bond, cineby.site, cineby.watch, cineby.digital.
"""

from __future__ import annotations

import asyncio

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "cineby.gd",
    "cineby.app",
    "cineby.xyz",
    "cineby.today",
    "cineby.bond",
    "cineby.site",
    "cineby.watch",
    "cineby.digital",
]
_API_BASE = "https://db.videasy.net"
_EMBED_BASE = "https://www.vidking.net"
_PER_PAGE = 20
_MAX_PAGES = 50  # 50 × 20 = 1000

# TMDB genre ID → name mapping
_GENRE_MAP: dict[int, str] = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Science Fiction",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
    # TV-specific genres
    10759: "Action & Adventure",
    10762: "Kids",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
}


class CinebyPlugin(HttpxPluginBase):
    """Python plugin for Cineby using httpx (TMDB API proxy + vidking embed)."""

    name = "cineby"
    provides = "stream"
    _domains = _DOMAINS

    categories = {
        2000: "Movies",
        5000: "TV",
    }

    async def _api_search(
        self,
        query: str,
        media_type: str | None = None,
    ) -> list[dict]:
        """Search the TMDB-proxy API with pagination.

        Args:
            query: Search term.
            media_type: ``"movie"``, ``"tv"``, or ``None`` for multi-search.

        Returns:
            List of raw result dicts (persons filtered out).
        """
        await self._ensure_client()
        all_results: list[dict] = []

        if media_type == "movie":
            endpoint = f"{_API_BASE}/3/search/movie"
        elif media_type == "tv":
            endpoint = f"{_API_BASE}/3/search/tv"
        else:
            endpoint = f"{_API_BASE}/3/search/multi"

        for page_num in range(1, _MAX_PAGES + 1):
            resp = await self._safe_fetch(
                f"{endpoint}?query={query}&language=en&page={page_num}",
                context=f"cineby_search_page_{page_num}",
            )
            if resp is None:
                break

            data = self._safe_parse_json(resp, context="cineby_search")
            if data is None:
                break

            results = data.get("results") or []

            # Filter out "person" results for multi-search
            if media_type is None:
                results = [r for r in results if r.get("media_type") in ("movie", "tv")]
            else:
                # Typed search endpoints don't include media_type; inject it
                for r in results:
                    r.setdefault("media_type", media_type)

            all_results.extend(results)

            if len(all_results) >= self.effective_max_results:
                break

            total_pages = data.get("total_pages", 1)
            if page_num >= total_pages:
                break

        self._log.info("cineby_search", query=query, count=len(all_results))
        return all_results[: self.effective_max_results]

    async def _fetch_detail(self, tmdb_id: int, media_type: str) -> dict | None:
        """Fetch movie/TV detail for IMDB ID and additional metadata."""
        resp = await self._safe_fetch(
            f"{_API_BASE}/3/{media_type}/{tmdb_id}?language=en",
            context=f"cineby_detail_{media_type}_{tmdb_id}",
        )
        if resp is None:
            return None
        return self._safe_parse_json(resp, context="cineby_detail")

    def _build_search_result(
        self,
        entry: dict,
        detail: dict | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult:
        """Build a ``SearchResult`` from a search entry and optional detail."""
        media_type = entry.get("media_type", "movie")
        tmdb_id = entry.get("id", 0)

        # Title
        title = entry.get("title") or entry.get("name") or ""
        date_str = entry.get("release_date") or entry.get("first_air_date") or ""
        year = date_str[:4] if len(date_str) >= 4 else ""
        display_title = f"{title} ({year})" if year else title

        # Category
        category = 2000 if media_type == "movie" else 5000

        # Embed URL (streaming link)
        if media_type == "tv":
            embed_url = f"{_EMBED_BASE}/embed/tv/{tmdb_id}"
            if season is not None and episode is not None:
                embed_url += f"/{season}/{episode}"
            elif season is not None:
                embed_url += f"/{season}/1"
        else:
            embed_url = f"{_EMBED_BASE}/embed/movie/{tmdb_id}"

        # Source URL on cineby
        source_url = f"https://www.{self._domains[0]}/{media_type}/{tmdb_id}"

        # Description
        description = entry.get("overview") or ""
        if len(description) > 300:
            description = description[:297] + "..."

        # Genres from search (genre IDs)
        genre_ids = entry.get("genre_ids") or []
        genres = [_GENRE_MAP[gid] for gid in genre_ids if gid in _GENRE_MAP]

        # Rating
        rating = entry.get("vote_average")
        rating_str = f"{rating:.1f}" if rating else ""

        # Poster
        poster = entry.get("poster_path") or ""
        if poster:
            poster = f"https://image.tmdb.org/t/p/w185{poster}"

        # Detail enrichment
        imdb_id = ""
        runtime = ""
        if detail:
            imdb_id = detail.get("imdb_id") or ""
            runtime_val = detail.get("runtime") or detail.get("episode_run_time")
            if isinstance(runtime_val, int) and runtime_val > 0:
                runtime = str(runtime_val)
            elif isinstance(runtime_val, list) and runtime_val:
                runtime = str(runtime_val[0])
            # Prefer full genre names from detail
            detail_genres = detail.get("genres") or []
            if detail_genres:
                genres = [g.get("name", "") for g in detail_genres if g.get("name")]

        return SearchResult(
            title=display_title,
            download_link=embed_url,
            source_url=source_url,
            published_date=date_str or None,
            category=category,
            description=description or None,
            metadata={
                "genres": ", ".join(genres) if genres else "",
                "rating": rating_str,
                "imdb_id": imdb_id,
                "tmdb_id": str(tmdb_id),
                "quality": "",
                "runtime": runtime,
                "poster": poster,
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Fetch detail for one search entry and build result."""
        tmdb_id = entry.get("id")
        if not tmdb_id:
            return None

        media_type = entry.get("media_type", "movie")
        if media_type not in ("movie", "tv"):
            return None

        async with sem:
            detail = await self._fetch_detail(tmdb_id, media_type)

        return self._build_search_result(entry, detail, season, episode)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search Cineby and return results with streaming embed links.

        Uses the TMDB-compatible API at db.videasy.net for search and
        details.  Streaming via vidking.net embed player.
        """
        if not query:
            return []

        # Determine media type filter from category
        media_type: str | None = None
        if category is not None:
            if 2000 <= category < 3000:
                media_type = "movie"
            elif 5000 <= category < 6000:
                media_type = "tv"
            else:
                return []

        await self._ensure_client()

        search_results = await self._api_search(query, media_type=media_type)
        if not search_results:
            return []

        # Fetch details with bounded concurrency
        sem = self._new_semaphore()
        tasks = [self._process_entry(e, sem, season, episode) for e in search_results]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = [sr for sr in task_results if sr is not None]

        return results[: self.effective_max_results]


plugin = CinebyPlugin()
