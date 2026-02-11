"""moflix-stream.xyz Python plugin for Scavengarr.

Scrapes moflix-stream.xyz (German streaming aggregator) via its REST API:
- GET /api/v1/search/{query}?query={query}&limit=20 for search
- GET /api/v1/titles/{id}?load=videos,genres for title details + video embeds
- Movies and TV series with TMDB metadata (rating, year, genres, IMDB ID)
- Video embed links from multiple hosters (doods.to, etc.)

Domain fallback: moflix-stream.xyz, moflix-stream.click
No authentication required.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

# Known domains in priority order.
_DOMAINS = [
    "moflix-stream.xyz",
    "moflix-stream.click",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_SEARCH_LIMIT = 20  # API hard cap


def _pre_filter_by_category(results: list[dict], category: int | None) -> list[dict]:
    """Filter search results by is_series based on Torznab category."""
    if category is None:
        return results
    if 5000 <= category < 6000:
        return [r for r in results if r.get("is_series", False)]
    if 2000 <= category < 3000:
        return [r for r in results if not r.get("is_series", False)]
    return results


class MoflixPlugin:
    """Python plugin for moflix-stream.xyz using httpx (REST API)."""

    name = "moflix"
    version = "1.0.0"
    mode = "httpx"

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
                    log.info("moflix_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("moflix_no_domain_reachable", fallback=_DOMAINS[0])

    async def _api_search(self, query: str) -> list[dict]:
        """Search the API and return raw result dicts."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/search/{query}",
                params={"query": query, "limit": _SEARCH_LIMIT},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("moflix_search_failed", query=query, error=str(exc))
            return []

        data = resp.json()
        results = data.get("results", [])

        log.info("moflix_search", query=query, count=len(results))
        return results

    async def _fetch_title_detail(self, title_id: int) -> dict | None:
        """Fetch title details with videos and genres."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/titles/{title_id}",
                params={"load": "videos,genres"},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("moflix_detail_failed", title_id=title_id, error=str(exc))
            return None

        data = resp.json()
        title = data.get("title")

        if title:
            log.info(
                "moflix_detail",
                title_id=title_id,
                name=title.get("name"),
                videos=len(title.get("videos") or []),
                genres=len(title.get("genres") or []),
            )

        return title

    def _build_search_result(
        self,
        search_entry: dict,
        detail: dict | None,
    ) -> SearchResult:
        """Build a SearchResult from search entry and optional detail data."""
        name = search_entry.get("name", "")
        year = search_entry.get("year")
        is_series = search_entry.get("is_series", False)
        title_id = search_entry.get("id")

        # Use detail data for videos and genres
        videos: list[dict] = []
        genres: list[str] = []
        if detail:
            videos = detail.get("videos") or []
            genres = [g.get("name", "") for g in (detail.get("genres") or [])]

        # Build display title
        display_title = f"{name} ({year})" if year else name

        # Category
        category = 5000 if is_series else 2000

        # Source URL (title page on the site)
        slug = name.lower().replace(" ", "-")
        source_url = f"{self.base_url}/titles/{title_id}/{slug}"

        # Download link: first video embed src, fallback to source URL
        download_link = source_url
        download_links: list[dict[str, str]] = []
        for video in videos:
            src = video.get("src", "")
            if src:
                hoster = video.get("name", "Mirror")
                quality = video.get("quality", "")
                label = f"{hoster} ({quality})" if quality else hoster
                download_links.append({"hoster": label, "link": src})
                if download_link == source_url:
                    download_link = src

        # Description
        desc = search_entry.get("description", "") or ""
        if len(desc) > 300:
            desc = desc[:297] + "..."

        # Metadata
        rating = search_entry.get("rating")
        runtime = search_entry.get("runtime")

        return SearchResult(
            title=display_title,
            download_link=download_link,
            download_links=download_links or None,
            source_url=source_url,
            published_date=str(year) if year else None,
            category=category,
            description=desc or None,
            metadata={
                "genres": ", ".join(genres) if genres else "",
                "rating": str(rating) if rating else "",
                "imdb_id": search_entry.get("imdb_id") or "",
                "tmdb_id": str(search_entry.get("tmdb_id") or ""),
                "runtime": str(runtime) if runtime else "",
                "poster": search_entry.get("poster") or "",
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
        category: int | None,
    ) -> SearchResult | None:
        """Fetch detail for one search entry and build result."""
        title_id = entry.get("id")
        if not title_id:
            return None

        async with sem:
            detail = await self._fetch_title_detail(title_id)

        sr = self._build_search_result(entry, detail)

        # Post-filter by category range
        if category is not None:
            cat_range = (category // 1000) * 1000
            if not (cat_range <= sr.category < cat_range + 1000):
                return None

        return sr

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search moflix-stream.xyz and return results with video embed links.

        Uses the site's REST API for search and title details.
        """
        if not query:
            return []

        # Accept movies (2xxx), TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        await self._ensure_client()
        await self._verify_domain()

        search_results = await self._api_search(query)
        if not search_results:
            return []

        search_results = _pre_filter_by_category(search_results, category)
        if not search_results:
            return []

        # Fetch detail pages with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        tasks = [self._process_entry(e, sem, category) for e in search_results]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= _MAX_RESULTS:
                    break

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = MoflixPlugin()
