"""movie4k.sx Python plugin for Scavengarr.

Scrapes movie4k.sx (German streaming aggregator) via its JSON REST API:
- GET /data/browse/?lang=2&keyword={query}&type=&page={n}&limit=20 for search
- GET /data/watch/?_id={id} for detail pages with stream links
- Movies and TV series with TMDB metadata (rating, year, genres, IMDB ID)
- Stream links from multiple hosters (streamtape, voe, doodstream, etc.)

Multi-domain support: movie4k.sx, movie4k.ag, movie4k.stream
No authentication required.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

# Official domains in priority order.
_DOMAINS = [
    "movie4k.sx",
    "movie4k.ag",
    "movie4k.stream",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_PAGE_SIZE = 20
_MAX_PAGES = 50  # 20/page * 50 = 1000

# lang=2 is German, lang=3 is English.
_LANG_DE = 2

_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

# Map Torznab genre sub-categories to movie4k genre strings.
_GENRE_MAP: dict[int, str] = {
    2010: "Action",
    2020: "Thriller",
    2030: "Sci-Fi",
    2040: "Komödie",
    2045: "Animation",
    2050: "Drama",
    2060: "Horror",
    5010: "Action",
    5020: "Thriller",
    5030: "Sci-Fi",
    5040: "Komödie",
    5050: "Drama",
    5060: "Horror",
    5070: "Animation",
    5080: "Dokumentation",
}


def _domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _type_for_category(category: int | None) -> str:
    """Map Torznab category to movie4k type parameter."""
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
    """Determine Torznab category from movie4k item data."""
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
) -> tuple[str, list[dict[str, str]]]:
    """Deduplicate streams and return (first_link, download_links)."""
    download_links: list[dict[str, str]] = []
    seen: set[str] = set()
    first_link = ""

    for stream in streams:
        stream_url = stream.get("stream", "")
        release = (stream.get("release") or "").strip()
        if not stream_url:
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
        tmdb = detail.get("tmdb", {})
        if tmdb:
            movie_details = tmdb.get("movie", {}).get("movie_details", {})
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


class Movie4kPlugin:
    """Python plugin for movie4k.sx using httpx (JSON API)."""

    name = "movie4k"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"
    default_language = "de"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self.base_url: str = f"https://{_DOMAINS[0]}"
        self._domain_verified = False

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create httpx client if not already running."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def _verify_domain(self) -> None:
        """Find and cache a working domain from the fallback list."""
        if self._domain_verified:
            return

        client = await self._ensure_client()
        for domain in _DOMAINS:
            url = f"https://{domain}/"
            try:
                resp = await client.head(url, timeout=5.0)
                if resp.status_code == 200:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    log.info("movie4k_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("movie4k_no_domain_reachable", fallback=_DOMAINS[0])

    async def _browse_page(
        self,
        keyword: str,
        type_filter: str,
        genre: str,
        page: int,
    ) -> tuple[list[dict], int]:
        """Fetch one page of browse results.

        Returns (movies, total_items).
        """
        client = await self._ensure_client()

        params: dict[str, str | int] = {
            "lang": _LANG_DE,
            "keyword": keyword,
            "year": "",
            "networks": "",
            "rating": "",
            "votes": "",
            "genre": genre,
            "country": "",
            "cast": "",
            "directors": "",
            "type": type_filter,
            "order_by": "",
            "page": page,
            "limit": _PAGE_SIZE,
        }

        try:
            resp = await client.get(
                f"{self.base_url}/data/browse/",
                params=params,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "movie4k_browse_failed",
                keyword=keyword,
                page=page,
                error=str(exc),
            )
            return [], 0

        data = resp.json()
        movies = data.get("movies", [])
        pager = data.get("pager", {})
        total = pager.get("totalItems", 0)

        log.info(
            "movie4k_browse_page",
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
        genre: str,
    ) -> list[dict]:
        """Fetch all browse pages up to _MAX_RESULTS."""
        first_page, total = await self._browse_page(keyword, type_filter, genre, page=1)
        if not first_page:
            return []

        all_movies: list[dict] = list(first_page)
        if total <= _PAGE_SIZE or len(all_movies) >= _MAX_RESULTS:
            return all_movies[:_MAX_RESULTS]

        total_pages = min((total + _PAGE_SIZE - 1) // _PAGE_SIZE, _MAX_PAGES)

        for page_num in range(2, total_pages + 1):
            if len(all_movies) >= _MAX_RESULTS:
                break
            page_movies, _ = await self._browse_page(
                keyword, type_filter, genre, page=page_num
            )
            if not page_movies:
                break
            all_movies.extend(page_movies)

        return all_movies[:_MAX_RESULTS]

    async def _fetch_detail(self, movie_id: str) -> dict | None:
        """Fetch detail data with streams for a movie/series."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/data/watch/",
                params={"_id": movie_id},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "movie4k_detail_failed",
                movie_id=movie_id,
                error=str(exc),
            )
            return None

        data = resp.json()
        streams = data.get("streams", [])
        log.info(
            "movie4k_detail",
            movie_id=movie_id,
            title=data.get("title", ""),
            streams=len(streams),
        )
        return data

    def _build_search_result(
        self,
        browse_entry: dict,
        detail: dict | None,
    ) -> SearchResult | None:
        """Build a SearchResult from browse entry + optional detail data."""
        title = browse_entry.get("title", "")
        if not title:
            return None

        movie_id = browse_entry.get("_id", "")
        year = browse_entry.get("year")
        slug = browse_entry.get("slug", "")

        # Collect streams from detail data
        streams: list[dict] = detail.get("streams", []) if detail else []
        if not streams:
            return None

        first_link, download_links = _collect_streams(streams)
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
                "movie4k_id": movie_id,
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
    ) -> SearchResult | None:
        """Fetch detail for one browse entry and build result."""
        movie_id = entry.get("_id")
        if not movie_id:
            return None

        async with sem:
            detail = await self._fetch_detail(movie_id)

        return self._build_search_result(entry, detail)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search movie4k and return results with stream links.

        Uses the site's JSON API for browse/search and detail pages.
        """
        if not query:
            return []

        # Accept movies (2xxx) and TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        await self._ensure_client()
        await self._verify_domain()

        type_filter = _type_for_category(category)
        genre = _GENRE_MAP.get(category, "") if category else ""

        browse_results = await self._browse_all(query, type_filter, genre)
        if not browse_results:
            return []

        # Fetch detail pages with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        tasks = [self._process_entry(e, sem) for e in browse_results]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[SearchResult] = []
        for sr in task_results:
            if isinstance(sr, SearchResult):
                results.append(sr)
                if len(results) >= _MAX_RESULTS:
                    break

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = Movie4kPlugin()
