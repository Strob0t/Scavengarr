"""megakino.org / megakino.to Python plugin for Scavengarr.

Scrapes megakino.org (German streaming aggregator) via its JSON REST API:
- GET /data/browse/?lang=2&keyword={query}&type=&page={n}&limit=20 for search
- GET /data/watch/?_id={id} for detail pages with stream links
- Movies and TV series with TMDB metadata (rating, year, genres, IMDB ID)
- Stream links from multiple hosters (streamtape, voe, doodstream, etc.)
- TV series: season in detail `s` field, episode in stream `e` field

Multi-domain support: megakino.org, megakino.to
No authentication required.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["megakino.org", "megakino.to"]
_PAGE_SIZE = 20
_MAX_PAGES = 50  # 20/page * 50 = 1000

# lang=2 is German, lang=3 is English.
_LANG_DE = 2

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})


def _domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _type_for_category(category: int | None) -> str:
    """Map Torznab category to API type parameter."""
    if category is None:
        return ""
    if category in _TV_CATEGORIES or 5000 <= category < 6000:
        return "tvseries"
    if category in _MOVIE_CATEGORIES or 2000 <= category < 3000:
        return "movies"
    return ""


def _normalize_genres(genres_raw: str | list) -> list[str]:
    """Normalize genres field to a list of lowercase strings."""
    if isinstance(genres_raw, list):
        return [g.lower() for g in genres_raw]
    return [g.strip().lower() for g in str(genres_raw).split(",")]


def _genres_to_str(genres_raw: str | list) -> str:
    """Convert genres field to a comma-separated display string."""
    if isinstance(genres_raw, list):
        return ", ".join(genres_raw)
    return str(genres_raw)


def _detect_category(movie: dict) -> int:
    """Determine Torznab category from item data."""
    tv = movie.get("tv", 0)
    if tv == 1:
        lower = _normalize_genres(movie.get("genres", ""))
        if "animation" in lower or "anime" in lower:
            return 5070
        if "dokumentation" in lower or "documentary" in lower:
            return 5080
        return 5000
    return 2000


def _collect_streams(
    streams: list[dict],
    *,
    episode: int | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Deduplicate streams and return (first_link, download_links).

    Skips streams marked as deleted.  When *episode* is given, only
    streams whose ``e`` field matches are included.
    """
    download_links: list[dict[str, str]] = []
    seen: set[str] = set()
    first_link = ""

    for stream in streams:
        # Skip deleted streams
        if stream.get("deleted") == 1:
            continue

        # Episode filter
        if episode is not None:
            stream_ep = stream.get("e")
            if stream_ep is not None and int(stream_ep) != episode:
                continue

        stream_url = stream.get("stream", "")
        release = (stream.get("release") or "").strip()
        if not stream_url:
            continue

        # Normalize protocol-relative URLs (e.g. //streamtape.com/...)
        if stream_url.startswith("//"):
            stream_url = f"https:{stream_url}"

        # Reject non-HTTP URLs (API sometimes returns garbage like "http-equiv=")
        if not stream_url.startswith(("http://", "https://")):
            continue

        dedup_key = f"{release}|{_domain_from_url(stream_url)}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        hoster = _domain_from_url(stream_url)
        label = f"{hoster}: {release}" if release else hoster
        download_links.append({"hoster": label, "link": stream_url})

        if not first_link:
            first_link = stream_url

    return first_link, download_links


def _extract_metadata(detail: dict | None, browse_entry: dict) -> dict[str, str]:
    """Extract metadata fields from detail or browse entry."""
    if detail:
        rating = str(detail.get("rating") or "")
        runtime = str(detail.get("runtime") or "").strip()
        genres_str = _genres_to_str(detail.get("genres", ""))
        imdb_id = str(detail.get("imdb_id") or "")
        tmdb_raw = detail.get("tmdb", {})
        # tmdb may be a JSON string (double-encoded in API response)
        if isinstance(tmdb_raw, str):
            try:
                tmdb_raw = json.loads(tmdb_raw)
            except (json.JSONDecodeError, TypeError):
                tmdb_raw = {}
        if isinstance(tmdb_raw, dict) and tmdb_raw:
            # movie may be a dict or a list of dicts
            movie = tmdb_raw.get("movie", {})
            if isinstance(movie, list):
                movie = movie[0] if movie else {}
            movie_details = (
                movie.get("movie_details", {}) if isinstance(movie, dict) else {}
            )
            if not imdb_id:
                imdb_id = str(movie_details.get("imdb_id") or "")
            if not rating:
                rating = str(movie_details.get("vote_average") or "")
        description = str(detail.get("storyline") or detail.get("overview") or "")
    else:
        genres_str = _genres_to_str(browse_entry.get("genres", ""))
        rating = str(browse_entry.get("rating") or "")
        runtime = ""
        imdb_id = ""
        description = ""

    if len(description) > 300:
        description = description[:297] + "..."

    return {
        "rating": rating,
        "runtime": runtime,
        "genres": genres_str,
        "imdb_id": imdb_id,
        "description": description,
    }


class MegakinoToPlugin(HttpxPluginBase):
    """Python plugin for megakino.org using httpx (JSON API)."""

    name = "megakino_to"
    provides = "stream"
    _domains = _DOMAINS

    async def _browse_page(
        self,
        keyword: str,
        type_filter: str,
        page: int,
    ) -> tuple[list[dict], int]:
        """Fetch one page of browse results.

        Returns (movies, total_items).
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/data/browse/",
            context="browse",
            params={
                "lang": _LANG_DE,
                "keyword": keyword,
                "year": "",
                "networks": "",
                "rating": "",
                "votes": "",
                "genre": "",
                "country": "",
                "cast": "",
                "directors": "",
                "type": type_filter,
                "order_by": "",
                "page": page,
                "limit": _PAGE_SIZE,
            },
        )
        if resp is None:
            return [], 0

        data = self._safe_parse_json(resp, context="browse")
        if not isinstance(data, dict):
            return [], 0

        movies = data.get("movies", [])
        if not isinstance(movies, list):
            movies = []
        pager = data.get("pager", {})
        total = pager.get("totalItems", 0)

        self._log.info(
            "megakino_to_browse_page",
            keyword=keyword,
            page=page,
            results=len(movies),
            total=total,
        )
        return movies, total

    async def _browse_all(
        self,
        keyword: str,
        type_filter: str,
    ) -> list[dict]:
        """Fetch all browse pages up to _max_results."""
        first_page, total = await self._browse_page(keyword, type_filter, page=1)
        if not first_page:
            return []

        all_movies: list[dict] = list(first_page)
        if total <= _PAGE_SIZE or len(all_movies) >= self.effective_max_results:
            return all_movies[: self.effective_max_results]

        total_pages = min((total + _PAGE_SIZE - 1) // _PAGE_SIZE, _MAX_PAGES)

        for page_num in range(2, total_pages + 1):
            if len(all_movies) >= self.effective_max_results:
                break
            page_movies, _ = await self._browse_page(
                keyword, type_filter, page=page_num
            )
            if not page_movies:
                break
            all_movies.extend(page_movies)

        return all_movies[: self.effective_max_results]

    async def _fetch_detail(self, movie_id: str) -> dict | None:
        """Fetch detail data with streams for a movie/series."""
        resp = await self._safe_fetch(
            f"{self.base_url}/data/watch/",
            context="detail",
            params={"_id": movie_id},
        )
        if resp is None:
            return None

        data = self._safe_parse_json(resp, context="detail")
        if not isinstance(data, dict):
            return None

        streams = data.get("streams", [])
        self._log.info(
            "megakino_to_detail",
            movie_id=movie_id,
            title=data.get("title", ""),
            streams=len(streams),
        )
        return data

    def _build_search_result(
        self,
        browse_entry: dict,
        detail: dict | None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Build a SearchResult from browse entry + optional detail data."""
        title = browse_entry.get("title", "")
        if not title:
            return None

        movie_id = browse_entry.get("_id", "")
        year = browse_entry.get("year")
        slug = browse_entry.get("slug", "")

        # Season filter: detail `s` field indicates the season number
        if season is not None and detail:
            detail_season = detail.get("s")
            if detail_season is not None and int(detail_season) != season:
                return None

        # Collect streams from detail data
        streams: list[dict] = detail.get("streams", []) if detail else []
        if not streams:
            return None

        first_link, download_links = _collect_streams(streams, episode=episode)
        if not first_link:
            return None

        category = _detect_category(detail if detail else browse_entry)
        display_title = f"{title} ({year})" if year else title
        source_url = (
            f"{self.base_url}/watch/{slug}/{movie_id}"
            if slug
            else f"{self.base_url}/watch/{movie_id}"
        )

        meta = _extract_metadata(detail, browse_entry)
        first_release = (streams[0].get("release") or "").strip()

        return SearchResult(
            title=display_title,
            download_link=first_link,
            download_links=download_links or None,
            source_url=source_url,
            published_date=str(year) if year else None,
            category=category,
            release_name=first_release or None,
            description=meta["description"] or None,
            metadata={
                "genres": meta["genres"],
                "rating": meta["rating"],
                "imdb_id": meta["imdb_id"],
                "runtime": meta["runtime"],
                "year": str(year) if year else "",
                "megakino_to_id": movie_id,
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Fetch detail for one browse entry and build result."""
        movie_id = entry.get("_id")
        if not movie_id:
            return None

        async with sem:
            detail = await self._fetch_detail(movie_id)

        return self._build_search_result(entry, detail, season=season, episode=episode)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search megakino.org and return results with stream links.

        Uses the site's JSON API for browse/search and detail pages.
        When *season* is provided, only TV series with matching season
        are returned and streams are filtered by *episode* if given.
        """
        if not query:
            return []

        # Accept movies (2xxx) and TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        # When season/episode are requested, restrict to series
        effective_category = category
        if season is not None and effective_category is None:
            effective_category = 5000

        await self._ensure_client()
        await self._verify_domain()

        type_filter = _type_for_category(effective_category)

        browse_results = await self._browse_all(query, type_filter)
        if not browse_results:
            return []

        # Fetch detail pages with bounded concurrency
        sem = self._new_semaphore()
        tasks = [
            self._process_entry(e, sem, season=season, episode=episode)
            for e in browse_results
        ]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[SearchResult] = []
        for sr in task_results:
            if isinstance(sr, SearchResult):
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results[: self.effective_max_results]


plugin = MegakinoToPlugin()
