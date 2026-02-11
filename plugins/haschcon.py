"""haschcon.com Python plugin for Scavengarr.

Scrapes haschcon.com (German movie review/streaming site) via WordPress REST API:
- GET /wp-json/wp/v2/aiovg_videos?search={query}&per_page=100&_embed for search
- GET /player-embed/id/{post_id}/ for YouTube/Dailymotion embed URLs
- Movies only, ~600+ videos with embedded YouTube/Dailymotion players
- Categories, tags (actors), reviews, featured images via _embed

WordPress AIOVG (All-in-One Video Gallery) plugin powers the video system.
No authentication required. No alternative domains.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_BASE_URL = "https://haschcon.com"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_PER_PAGE = 100  # WP REST API max
_MAX_PAGES = _MAX_RESULTS // _PER_PAGE  # 10 pages × 100 = 1000

# Regex patterns for extracting video URLs from player embed pages.
_YT_PATTERN = re.compile(r"youtube\.com/embed/([a-zA-Z0-9_-]+)")
_DM_PATTERN = re.compile(r"dailymotion\.com/embed/video/([a-zA-Z0-9]+)")


class HaschconPlugin:
    """Python plugin for haschcon.com using httpx (WordPress REST API)."""

    name = "haschcon"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"
    default_language = "de"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self.base_url: str = _BASE_URL

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create httpx client if not already running."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def _api_search(self, query: str) -> list[dict]:
        """Search via WP REST API across multiple pages."""
        client = await self._ensure_client()
        all_entries: list[dict] = []

        for page in range(1, _MAX_PAGES + 1):
            try:
                resp = await client.get(
                    f"{self.base_url}/wp-json/wp/v2/aiovg_videos",
                    params={
                        "search": query,
                        "per_page": str(_PER_PAGE),
                        "page": str(page),
                        "_embed": "1",
                    },
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "haschcon_search_failed",
                    query=query,
                    page=page,
                    error=str(exc),
                )
                break

            entries = resp.json()
            if not entries:
                break

            all_entries.extend(entries)

            if len(all_entries) >= _MAX_RESULTS:
                break

            # Check pagination headers
            total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
            if page >= total_pages:
                break

        log.info("haschcon_search", query=query, count=len(all_entries))
        return all_entries[:_MAX_RESULTS]

    async def _fetch_player_embed(self, post_id: int) -> str | None:
        """Fetch player embed page and extract YouTube/Dailymotion URL."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/player-embed/id/{post_id}/",
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("haschcon_player_failed", post_id=post_id, error=str(exc))
            return None

        text = resp.text

        # Try YouTube first
        yt_match = _YT_PATTERN.search(text)
        if yt_match:
            return f"https://www.youtube.com/watch?v={yt_match.group(1)}"

        # Try Dailymotion
        dm_match = _DM_PATTERN.search(text)
        if dm_match:
            return f"https://www.dailymotion.com/video/{dm_match.group(1)}"

        return None

    def _build_search_result(
        self,
        entry: dict,
        video_url: str | None,
    ) -> SearchResult:
        """Build a SearchResult from a WP REST API video entry."""
        title = html_lib.unescape(entry.get("title", {}).get("rendered", ""))
        link = entry.get("link", "")
        date = entry.get("date", "")

        # Extract year from date (format: "2026-01-27T16:14:10")
        year = date[:4] if date and len(date) >= 4 else None

        # Source URL
        source_url = link or f"{self.base_url}/video/{entry.get('slug', '')}/"

        # Download link: video URL or source page
        download_link = video_url or source_url

        # Extract categories and tags from _embedded
        embedded = entry.get("_embedded", {})
        terms = embedded.get("wp:term", [])
        categories: list[str] = []
        tags: list[str] = []
        if len(terms) > 0 and terms[0]:
            categories = [t.get("name", "") for t in terms[0] if t.get("name")]
        if len(terms) > 1 and terms[1]:
            tags = [t.get("name", "") for t in terms[1] if t.get("name")]

        # Featured image
        featured_media = embedded.get("wp:featuredmedia", [])
        poster = ""
        if featured_media and featured_media[0]:
            poster = featured_media[0].get("source_url", "") or ""

        # Excerpt as description (strip HTML tags)
        excerpt_html = entry.get("excerpt", {}).get("rendered", "") or ""
        desc = re.sub(r"<[^>]+>", "", excerpt_html).strip()
        if len(desc) > 300:
            desc = desc[:297] + "..."

        return SearchResult(
            title=title,
            download_link=download_link,
            source_url=source_url,
            published_date=year,
            category=2000,  # Movies only
            description=desc or None,
            metadata={
                "genres": ", ".join(categories) if categories else "",
                "actors": ", ".join(tags) if tags else "",
                "poster": poster,
            },
        )

    async def _process_entry(
        self,
        entry: dict,
        sem: asyncio.Semaphore,
    ) -> SearchResult | None:
        """Fetch player embed for one entry and build result."""
        post_id = entry.get("id")
        if not post_id:
            return None

        async with sem:
            video_url = await self._fetch_player_embed(post_id)

        return self._build_search_result(entry, video_url)

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search haschcon.com and return results with video links.

        Uses the WordPress REST API for search and player embed pages
        for YouTube/Dailymotion video URLs. Movies only (category 2000-2999).
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

        # Fetch player embeds with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        tasks = [self._process_entry(e, sem) for e in search_results]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = [sr for sr in task_results if sr is not None]

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = HaschconPlugin()
