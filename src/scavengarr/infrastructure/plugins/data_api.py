"""Shared base for sites built on the same "/data" JSON API.

megakino.org/.to and movie4k.sx/.ag/.stream run the same backend:

- ``GET /data/browse/?lang=2&keyword={query}&type=&page={n}&limit=20`` searches
- ``GET /data/watch/?_id={id}`` returns a title with its stream links
- series: season in the detail ``s`` field, episode in each stream's ``e``
- streams carry ``deleted: 1`` when the hoster removed them
- ``tmdb`` may be a dict or a JSON string, ``tmdb.movie`` a dict or a list

The plugins used to be two copies of this code; the movie4k copy had lost the
episode filter, the deleted-stream check and the tolerant TMDB parsing. A
plugin only sets ``name``, ``provides`` and ``_domains``.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

PAGE_SIZE = 20
MAX_PAGES = 50  # 20/page * 50 = 1000

# lang=2 is German, lang=3 is English.
LANG_DE = 2


def domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def type_for_category(category: int | None) -> str:
    """Map Torznab category to the API ``type`` parameter."""
    if category is None:
        return ""
    if 5000 <= category < 6000:
        return "tvseries"
    if 2000 <= category < 3000:
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


def detect_category(movie: dict) -> int:
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


def collect_streams(
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

        dedup_key = f"{release}|{domain_from_url(stream_url)}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        hoster = domain_from_url(stream_url)
        label = f"{hoster}: {release}" if release else hoster
        download_links.append({"hoster": label, "link": stream_url})

        if not first_link:
            first_link = stream_url

    return first_link, download_links


def _tmdb_movie_details(tmdb_raw: object) -> dict:
    """``tmdb.movie.movie_details`` from a dict or JSON string, movie as dict
    or list; ``{}`` when anything is missing."""
    # tmdb may be a JSON string (double-encoded in API response)
    if isinstance(tmdb_raw, str):
        try:
            tmdb_raw = json.loads(tmdb_raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(tmdb_raw, dict):
        return {}
    movie = tmdb_raw.get("movie", {})
    if isinstance(movie, list):
        movie = movie[0] if movie else {}
    details = movie.get("movie_details", {}) if isinstance(movie, dict) else {}
    return details if isinstance(details, dict) else {}


def extract_metadata(detail: dict | None, browse_entry: dict) -> dict[str, str]:
    """Extract metadata fields from detail or browse entry."""
    if detail:
        rating = str(detail.get("rating") or "")
        runtime = str(detail.get("runtime") or "").strip()
        genres_str = _genres_to_str(detail.get("genres", ""))
        imdb_id = str(detail.get("imdb_id") or "")
        movie_details = _tmdb_movie_details(detail.get("tmdb", {}))
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


class DataApiPluginBase(HttpxPluginBase):
    """Search/detail logic for "/data" API sites; subclasses set identity."""

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
                "lang": LANG_DE,
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
                "limit": PAGE_SIZE,
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
        total = pager.get("totalItems", 0) if isinstance(pager, dict) else 0

        self._log.info(
            f"{self.name}_browse_page",
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
        if total <= PAGE_SIZE or len(all_movies) >= self.effective_max_results:
            return all_movies[: self.effective_max_results]

        total_pages = min((total + PAGE_SIZE - 1) // PAGE_SIZE, MAX_PAGES)

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
            f"{self.name}_detail",
            movie_id=movie_id,
            title=data.get("title", ""),
            streams=len(streams) if isinstance(streams, list) else 0,
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
        streams = detail.get("streams", []) if detail else []
        if not isinstance(streams, list) or not streams:
            return None

        first_link, download_links = collect_streams(streams, episode=episode)
        if not first_link:
            return None

        category = detect_category(detail if detail else browse_entry)
        display_title = f"{title} ({year})" if year else title
        source_url = (
            f"{self.base_url}/watch/{slug}/{movie_id}"
            if slug
            else f"{self.base_url}/watch/{movie_id}"
        )

        meta = extract_metadata(detail, browse_entry)
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
                f"{self.name}_id": movie_id,
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
        """Search the site and return results with stream links.

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

        browse_results = await self._browse_all(
            query, type_for_category(effective_category)
        )
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
            elif isinstance(sr, BaseException):
                self._log.warning(f"{self.name}_entry_failed", error=repr(sr))
            if len(results) >= self.effective_max_results:
                break

        return results[: self.effective_max_results]
