"""einschalten.in Python plugin for Scavengarr.

Scrapes einschalten.in (German streaming site) via its JSON API:
- POST /api/search for movie search
- GET /api/movies/{id} for movie detail (genres, IMDB ID, runtime, overview)
- GET /api/movies/{id}/watch for stream URL and release name

Movies only (no TV series). TMDB metadata (rating, year, genres, IMDB ID).
Single domain: einschalten.in (no known alternatives).
No authentication required.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["einschalten.in"]
_MAX_PAGES = 32  # ~32 results/page → 32 pages for ~1000


def _extract_year(release_date: str | None) -> str | None:
    """Extract 4-digit year from a date string like '2022-03-01'."""
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        return release_date[:4]
    return None


def _hoster_from_url(url: str) -> str:
    """Extract hostname from a stream URL for display."""
    hostname = urlparse(url).hostname
    return hostname or "Stream"


class EinschaltenPlugin(HttpxPluginBase):
    """Python plugin for einschalten.in using httpx (JSON API)."""

    name = "einschalten"
    provides = "stream"
    _domains = _DOMAINS

    async def _api_search(self, query: str) -> list[dict]:
        """Search the API and return all unique results across pages."""
        all_results: list[dict] = []
        seen_ids: set[int] = set()

        for page in range(1, _MAX_PAGES + 1):
            resp = await self._safe_fetch(
                f"{self.base_url}/api/search",
                method="POST",
                json={"query": query, "page": page},
                context="search",
            )
            if resp is None:
                break

            data = self._safe_parse_json(resp, context="search")
            if not isinstance(data, dict):
                break

            items = data.get("data") or []
            pagination = data.get("pagination") or {}

            new_count = 0
            for item in items:
                mid = item.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_results.append(item)
                    new_count += 1

            if new_count == 0 or not pagination.get("hasMore", False):
                break

            if len(all_results) >= self.effective_max_results:
                break

        self._log.info("einschalten_search", query=query, count=len(all_results))
        return all_results[: self.effective_max_results]

    async def _fetch_detail(self, movie_id: int) -> dict | None:
        """Fetch movie detail (metadata, genres, IMDB ID)."""
        resp = await self._safe_fetch(
            f"{self.base_url}/api/movies/{movie_id}",
            context="detail",
        )
        if resp is None:
            return None
        data = self._safe_parse_json(resp, context="detail")
        return data if isinstance(data, dict) else None

    async def _fetch_watch(self, movie_id: int) -> dict | None:
        """Fetch watch info (stream URL + release name)."""
        resp = await self._safe_fetch(
            f"{self.base_url}/api/movies/{movie_id}/watch",
            context="watch",
        )
        if resp is None:
            return None
        data = self._safe_parse_json(resp, context="watch")
        return data if isinstance(data, dict) else None

    def _build_search_result(
        self,
        search_entry: dict,
        detail: dict | None,
        watch: dict | None,
    ) -> SearchResult:
        """Build a SearchResult from search entry, detail, and watch data."""
        title = search_entry.get("title", "")
        movie_id = search_entry.get("id")

        # Extract year from releaseDate (e.g. "2022-03-01")
        release_date = search_entry.get("releaseDate", "") or ""
        year = _extract_year(release_date)

        # Display title
        display_title = f"{title} ({year})" if year else title

        # Source URL
        source_url = f"{self.base_url}/movies/{movie_id}"

        # Stream URL from watch endpoint
        stream_url = ""
        release_name = None
        if watch:
            stream_url = watch.get("streamUrl", "") or ""
            release_name = watch.get("releaseName") or None

        download_link = stream_url or source_url

        # Download links list
        download_links: list[dict[str, str]] | None = None
        if stream_url:
            hoster = _hoster_from_url(stream_url)
            download_links = [{"hoster": hoster, "link": stream_url}]

        # Detail metadata
        genres: list[str] = []
        imdb_id = ""
        runtime = ""
        overview = ""
        rating = ""
        poster = search_entry.get("posterPath", "") or ""

        if detail:
            genres = [g.get("name", "") for g in (detail.get("genres") or [])]
            imdb_id = detail.get("imdbId", "") or ""
            runtime = str(detail.get("runtime")) if detail.get("runtime") else ""
            overview = detail.get("overview", "") or ""
            poster = detail.get("posterPath", "") or poster
            vote_avg = detail.get("voteAverage")
            if vote_avg:
                rating = str(vote_avg)

        # Truncate description
        if len(overview) > 300:
            overview = overview[:297] + "..."

        return SearchResult(
            title=display_title,
            download_link=download_link,
            download_links=download_links,
            source_url=source_url,
            release_name=release_name,
            published_date=year,
            category=2000,  # Movies only
            description=overview or None,
            metadata={
                "genres": ", ".join(genres) if genres else "",
                "rating": rating,
                "imdb_id": imdb_id,
                "tmdb_id": str(movie_id) if movie_id else "",
                "runtime": runtime,
                "poster": poster,
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
    ) -> SearchResult | None:
        """Fetch detail + watch for one search entry and build result."""
        movie_id = entry.get("id")
        if not movie_id:
            return None

        async with sem:
            detail, watch = await asyncio.gather(
                self._fetch_detail(movie_id),
                self._fetch_watch(movie_id),
            )

        return self._build_search_result(entry, detail, watch)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search einschalten.in and return results with stream links.

        Uses the site's JSON API for search, movie details, and stream URLs.
        Movies only — TV categories are rejected.
        """
        if not query:
            return []

        # Movies only (2xxx)
        if category is not None and not (2000 <= category < 3000):
            return []

        await self._ensure_client()

        search_results = await self._api_search(query)
        if not search_results:
            return []

        # Fetch detail + watch pages with bounded concurrency
        sem = self._new_semaphore()
        tasks = [self._process_entry(e, sem) for e in search_results]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results[: self.effective_max_results]


plugin = EinschaltenPlugin()
