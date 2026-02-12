"""cine.to Python plugin for Scavengarr.

Scrapes cine.to (German movie streaming aggregator) via its POST-based REST API:
- POST /request/search for search (form-urlencoded, paginated, 24 results/page)
- POST /request/entry for title details (genres, rating, plot, duration)
- POST /request/links for stream hoster links (redirect via /out/{link_id})

Movies only (no TV series). Results include IMDB IDs and multiple hoster links.
No authentication required. DDoS-Guard cookies not needed for API access.
"""

from __future__ import annotations

import asyncio

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

_PER_PAGE = 24
_MAX_PAGES = 41  # 1000 // 24 = 41 pages × 24 = 984, +1 page = 1008

_QUALITY_MAP: dict[int | str, str] = {
    0: "CAM",
    1: "TS",
    2: "DVD",
    3: "HD",
    "0": "CAM",
    "1": "TS",
    "2": "DVD",
    "3": "HD",
}


class CinePlugin(HttpxPluginBase):
    """Python plugin for cine.to using httpx (REST API, movies only)."""

    name = "cine"
    provides = "stream"
    _domains = ["cine.to"]

    async def _api_search(self, query: str) -> list[dict]:
        """Search the API across multiple pages and return raw entry dicts."""
        client = await self._ensure_client()
        all_entries: list[dict] = []

        for page in range(1, _MAX_PAGES + 2):
            try:
                resp = await client.post(
                    f"{self.base_url}/request/search",
                    data={
                        "term": query,
                        "kind": "all",
                        "genre": "0",
                        "rating": "1",
                        "year[]": ["1902", "2026"],
                        "language": "0",
                        "page": str(page),
                        "count": str(_PER_PAGE),
                    },
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "cine_search_failed",
                    query=query,
                    page=page,
                    error=str(exc),
                )
                break

            data = resp.json()
            if not data.get("status"):
                break

            entries = data.get("entries") or []
            all_entries.extend(entries)

            if len(all_entries) >= self._max_results:
                break

            total_pages = data.get("pages", 1)
            if page >= total_pages:
                break

        self._log.info("cine_search", query=query, count=len(all_entries))
        return all_entries[: self._max_results]

    async def _fetch_entry_detail(self, imdb_id: str) -> dict | None:
        """Fetch title details (genres, rating, plot, duration)."""
        client = await self._ensure_client()

        try:
            resp = await client.post(
                f"{self.base_url}/request/entry",
                data={"ID": imdb_id},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("cine_detail_failed", imdb_id=imdb_id, error=str(exc))
            return None

        data = resp.json()
        if not data.get("status"):
            return None

        return data.get("entry")

    async def _fetch_links(self, imdb_id: str) -> dict | None:
        """Fetch stream hoster links for a title."""
        client = await self._ensure_client()

        try:
            resp = await client.post(
                f"{self.base_url}/request/links",
                data={"ID": imdb_id},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("cine_links_failed", imdb_id=imdb_id, error=str(exc))
            return None

        data = resp.json()
        if not data.get("status"):
            return None

        return data.get("links")

    def _build_search_result(
        self,
        search_entry: dict,
        detail: dict | None,
        links: dict | None,
    ) -> SearchResult:
        """Build a SearchResult from search entry, detail, and links data."""
        title = search_entry.get("title", "")
        year = search_entry.get("year")
        imdb_id = search_entry.get("imdb", "")
        quality_code = search_entry.get("quality")

        # Display title
        display_title = f"{title} ({year})" if year else title

        # Quality label from search entry
        quality = _QUALITY_MAP.get(quality_code, "")

        # Source URL
        source_url = f"{self.base_url}/#tt{imdb_id}" if imdb_id else self.base_url

        # Build download links from hoster data
        download_link = source_url
        download_links: list[dict[str, str]] = []
        if links:
            for hoster_name, link_data in links.items():
                if not isinstance(link_data, list) or len(link_data) < 2:
                    continue
                hoster_quality = _QUALITY_MAP.get(link_data[0], "")
                for link_id in link_data[1:]:
                    link_url = f"{self.base_url}/out/{link_id}"
                    label = (
                        f"{hoster_name} ({hoster_quality})"
                        if hoster_quality
                        else hoster_name
                    )
                    download_links.append({"hoster": label, "link": link_url})
                    if download_link == source_url:
                        download_link = link_url

        # Detail data
        description = ""
        genres: list[str] = []
        rating = ""
        duration = ""
        cover = ""
        if detail:
            # Prefer German plot, fall back to English
            description = detail.get("plot_de", "") or detail.get("plot_en", "") or ""
            genres = detail.get("genres") or []
            rating_val = detail.get("rating")
            if rating_val:
                rating = str(rating_val)
            duration_val = detail.get("duration")
            if duration_val:
                duration = str(duration_val)
            cover = detail.get("cover", "") or ""

        if len(description) > 300:
            description = description[:297] + "..."

        return SearchResult(
            title=display_title,
            download_link=download_link,
            download_links=download_links or None,
            source_url=source_url,
            published_date=str(year) if year else None,
            category=2000,  # Movies only
            description=description or None,
            metadata={
                "genres": ", ".join(genres) if genres else "",
                "rating": rating,
                "imdb_id": str(imdb_id) if imdb_id else "",
                "quality": quality,
                "runtime": duration,
                "poster": cover,
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
    ) -> SearchResult | None:
        """Fetch detail and links for one search entry and build result."""
        imdb_id = entry.get("imdb")
        if not imdb_id:
            return None

        imdb_str = str(imdb_id)

        async with sem:
            detail, links = await asyncio.gather(
                self._fetch_entry_detail(imdb_str),
                self._fetch_links(imdb_str),
            )

        return self._build_search_result(entry, detail, links)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search cine.to and return results with stream hoster links.

        Uses the site's POST-based REST API for search, details, and links.
        Movies only (category 2000-2999).
        """
        if not query:
            return []

        # Only accept movie categories (2xxx)
        if category is not None and not (2000 <= category < 3000):
            return []

        await self._ensure_client()

        search_results = await self._api_search(query)
        if not search_results:
            return []

        # Fetch detail + links with bounded concurrency
        sem = self._new_semaphore()
        tasks = [self._process_entry(e, sem) for e in search_results]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = [sr for sr in task_results if sr is not None]

        return results[: self._max_results]


plugin = CinePlugin()
