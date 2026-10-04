"""moflix-stream.xyz Python plugin for Scavengarr.

Scrapes moflix-stream.xyz (German streaming aggregator) through its REST API:
- GET / once for the site's Laravel session (XSRF-TOKEN and session cookies)
- GET /api/v1/search/{query}?query={query}&limit=20 for search
- GET /api/v1/titles/{id}?load=videos,genres for title details + video embeds
- API calls send ``Accept: application/json``, the site as ``Referer`` and the
  XSRF-TOKEN cookie as ``X-XSRF-TOKEN`` (the API answers 401 without them)
- Movies and TV series with TMDB metadata (rating, year, genres, IMDB ID)
- Video embed links from multiple hosters (doods.to, etc.)

Cloudflare challenges the API for some IPs (a VPN, at times a home line):
``_fetch_text()`` lets the browser pass it once, then the calls go on over
httpx with the browser's session. Until 1.2.0 the plugin ran every search in
the browser, too slow for Stremio on a Raspberry Pi.

Domain fallback: moflix-stream.xyz, moflix-stream.click
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, unquote, urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "moflix-stream.xyz",
    "moflix-stream.click",
]
_SEARCH_LIMIT = 20  # API hard cap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _is_premium(video: dict) -> bool:
    """moflix's paid player ("Premium (No Ads)", HLS on moflix-stream.day).

    Its master playlist answers anyone, every variant 403s without a paid
    session, so no player can play it.
    """
    return "premium" in str(video.get("name") or "").lower()


def _pre_filter_by_category(results: list[dict], category: int | None) -> list[dict]:
    """Filter search results by is_series based on Torznab category."""
    if category is None:
        return results
    if 5000 <= category < 6000:
        return [r for r in results if r.get("is_series", False)]
    if 2000 <= category < 3000:
        return [r for r in results if not r.get("is_series", False)]
    return results


class MoflixPlugin(HttpxPluginBase):
    """Python plugin for moflix-stream.xyz (JSON API over httpx)."""

    name = "moflix"
    version = "1.2.0"
    provides = "stream"

    _domains = _DOMAINS

    def _site_cookie(self, name: str) -> str | None:
        """The value of the site's cookie *name* in the HTTP client's jar."""
        if self._client is None:
            return None
        host = urlparse(self.base_url).hostname or ""
        for cookie in self._client.cookies.jar:
            if cookie.name == name and cookie.domain.lstrip(".") == host:
                return cookie.value
        return None

    def _api_headers(self) -> dict[str, str]:
        """What the site's own app sends with its API calls."""
        headers = {"Accept": "application/json", "Referer": f"{self.base_url}/"}
        token = self._site_cookie("XSRF-TOKEN")
        if token:
            headers["X-XSRF-TOKEN"] = unquote(token)
        return headers

    async def _ensure_session(self) -> None:
        """Start the site's session once (the homepage sets its cookies)."""
        await self._ensure_client()
        if self._site_cookie("XSRF-TOKEN") is None:
            await self._fetch_text(f"{self.base_url}/", context="session")

    async def _api_fetch(self, path: str) -> dict[str, Any] | None:
        """Call a moflix API endpoint (``None`` on failure)."""
        body = await self._fetch_text(
            f"{self.base_url}{path}", context="api", headers=self._api_headers()
        )
        return self._parse_json_text(body, context="api")

    async def _api_search(self, query: str) -> list[dict]:
        """Search the API and return raw result dicts."""
        encoded = quote(query, safe="")
        path = f"/api/v1/search/{encoded}?query={encoded}&limit={_SEARCH_LIMIT}"

        data = await self._api_fetch(path)
        if not data:
            return []

        results = data.get("results", [])
        self._log.info("moflix_search", query=query, count=len(results))
        return results

    async def _fetch_title_detail(self, title_id: int) -> dict | None:
        """Fetch title details with videos and genres."""
        path = f"/api/v1/titles/{title_id}?load=videos,genres"
        data = await self._api_fetch(path)
        if not data:
            return None

        title = data.get("title")
        if title:
            self._log.info(
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
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Build a SearchResult from search entry and detail data.

        Returns ``None`` without playable videos: the site's own title page
        is neither a download link nor a stream. For a season/episode request
        only videos of that season/episode count.
        """
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
        if season is not None:
            videos = [
                v
                for v in videos
                if v.get("season_num") == season
                and (episode is None or v.get("episode_num") == episode)
            ]

        # Build display title
        display_title = f"{name} ({year})" if year else name

        # Category
        category = 5000 if is_series else 2000

        # Source URL (title page on the site)
        slug = name.lower().replace(" ", "-")
        source_url = f"{self.base_url}/titles/{title_id}/{slug}"

        download_links: list[dict[str, str]] = []
        for video in videos:
            src = video.get("src", "")
            if src and not _is_premium(video):
                hoster = video.get("name", "Mirror")
                quality = video.get("quality", "")
                label = f"{hoster} ({quality})" if quality else hoster
                download_links.append({"hoster": label, "link": src})
        if not download_links:
            self._log.debug("moflix_no_videos", title_id=title_id)
            return None

        # Description
        desc = search_entry.get("description", "") or ""
        if len(desc) > 300:
            desc = desc[:297] + "..."

        # Metadata
        rating = search_entry.get("rating")
        runtime = search_entry.get("runtime")

        return SearchResult(
            title=display_title,
            download_link=download_links[0]["link"],
            download_links=download_links,
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
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Fetch detail for one search entry and build result."""
        title_id = entry.get("id")
        if not title_id:
            return None

        async with sem:
            detail = await self._fetch_title_detail(title_id)

        sr = self._build_search_result(entry, detail, season, episode)
        if sr is None:
            return None

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
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search moflix-stream.xyz and return results with video embed links.

        Calls the site's REST API with the site's session (see
        ``_ensure_session()``).
        When *season* is provided, only series results are returned.
        """
        if not query:
            return []

        # Accept movies (2xxx), TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        await self._verify_domain()
        await self._ensure_session()

        search_results = await self._api_search(query)
        if not search_results:
            return []

        # When season/episode are requested, restrict to series
        effective_category = category
        if season is not None and effective_category is None:
            effective_category = 5000

        search_results = _pre_filter_by_category(search_results, effective_category)
        if not search_results:
            return []

        # Fetch detail pages with bounded concurrency
        sem = self._new_semaphore()
        tasks = [
            self._process_entry(e, sem, effective_category, season, episode)
            for e in search_results
        ]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results[: self.effective_max_results]


plugin = MoflixPlugin()
