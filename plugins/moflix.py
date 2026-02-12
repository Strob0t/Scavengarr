"""moflix-stream.xyz Python plugin for Scavengarr.

Scrapes moflix-stream.xyz (German streaming aggregator) via Playwright + REST API:
- Playwright solves the Cloudflare JS challenge and obtains an XSRF-TOKEN cookie
- API calls are executed from within the browser context using fetch()
- GET /api/v1/search/{query}?query={query}&limit=20 for search
- GET /api/v1/titles/{id}?load=videos,genres for title details + video embeds
- Movies and TV series with TMDB metadata (rating, year, genres, IMDB ID)
- Video embed links from multiple hosters (doods.to, etc.)

Domain fallback: moflix-stream.xyz, moflix-stream.click
Cloudflare JS challenge requires browser-based access (Playwright mode).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

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
_CF_TIMEOUT = 30_000  # ms to wait for Cloudflare challenge
_NAV_TIMEOUT = 30_000


def _pre_filter_by_category(results: list[dict], category: int | None) -> list[dict]:
    """Filter search results by is_series based on Torznab category."""
    if category is None:
        return results
    if 5000 <= category < 6000:
        return [r for r in results if r.get("is_series", False)]
    if 2000 <= category < 3000:
        return [r for r in results if not r.get("is_series", False)]
    return results


# JavaScript executed in the browser to call the API with proper XSRF headers.
_API_FETCH_JS = """
async (url) => {
    const token = document.cookie.match(/XSRF-TOKEN=([^;]+)/);
    const headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    };
    if (token) {
        headers['X-XSRF-TOKEN'] = decodeURIComponent(token[1]);
    }
    const resp = await fetch(url, { headers });
    if (!resp.ok) return { _error: resp.status };
    return await resp.json();
}
"""


class MoflixPlugin:
    """Python plugin for moflix-stream.xyz using Playwright (Cloudflare bypass)."""

    name = "moflix"
    version = "1.1.0"
    mode = "playwright"
    provides = "stream"
    default_language = "de"

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self.base_url: str = f"https://{_DOMAINS[0]}"
        self._domain_verified = False

    async def _ensure_browser(self) -> Browser:
        """Launch browser if not already running."""
        if self._browser is None:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
            log.info("moflix_browser_launched")
        return self._browser

    async def _ensure_context(self) -> BrowserContext:
        """Create browser context with proper user agent."""
        if self._context is None:
            browser = await self._ensure_browser()
            self._context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 720},
            )
        return self._context

    async def _ensure_page(self) -> Page:
        """Get or create the persistent page used for API calls."""
        if self._page is None or self._page.is_closed():
            ctx = await self._ensure_context()
            self._page = await ctx.new_page()
            self._page.set_default_navigation_timeout(_NAV_TIMEOUT)
            self._page.set_default_timeout(_NAV_TIMEOUT)
        return self._page

    async def _wait_for_cloudflare(self, page: Page) -> bool:
        """Wait for the Cloudflare JS challenge to resolve."""
        try:
            # The real site has a progress bar or content that loads after
            # the challenge.  Wait for the XSRF-TOKEN cookie to appear.
            for _ in range(30):
                cookies = await page.context.cookies()
                for c in cookies:
                    if c["name"] == "XSRF-TOKEN":
                        return True
                await page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001
            pass

        log.warning("moflix_cloudflare_timeout")
        return False

    async def _verify_domain(self) -> None:
        """Navigate to a working domain and solve the Cloudflare challenge."""
        if self._domain_verified:
            return

        page = await self._ensure_page()

        for domain in _DOMAINS:
            url = f"https://{domain}/"
            try:
                await page.goto(url, wait_until="domcontentloaded")
                if await self._wait_for_cloudflare(page):
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    log.info("moflix_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("moflix_no_domain_reachable", fallback=_DOMAINS[0])

    async def _api_fetch(self, path: str) -> dict[str, Any] | None:
        """Call a moflix API endpoint from within the browser context."""
        page = await self._ensure_page()
        url = f"{self.base_url}{path}"

        try:
            data = await page.evaluate(_API_FETCH_JS, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("moflix_api_fetch_failed", path=path, error=str(exc))
            return None

        if not isinstance(data, dict):
            return None

        if "_error" in data:
            log.warning("moflix_api_error", path=path, status=data["_error"])
            return None

        return data

    async def _api_search(self, query: str) -> list[dict]:
        """Search the API and return raw result dicts."""
        # URL-encode the query for the path segment
        from urllib.parse import quote

        encoded = quote(query, safe="")
        path = f"/api/v1/search/{encoded}?query={encoded}&limit={_SEARCH_LIMIT}"

        data = await self._api_fetch(path)
        if not data:
            return []

        results = data.get("results", [])
        log.info("moflix_search", query=query, count=len(results))
        return results

    async def _fetch_title_detail(self, title_id: int) -> dict | None:
        """Fetch title details with videos and genres."""
        path = f"/api/v1/titles/{title_id}?load=videos,genres"
        data = await self._api_fetch(path)
        if not data:
            return None

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
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search moflix-stream.xyz and return results with video embed links.

        Uses Playwright to bypass Cloudflare, then calls the site's REST API
        from within the browser context.
        When *season* is provided, only series results are returned.
        """
        if not query:
            return []

        # Accept movies (2xxx), TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        await self._verify_domain()

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
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        tasks = [
            self._process_entry(e, sem, effective_category) for e in search_results
        ]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= _MAX_RESULTS:
                    break

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close browser and Playwright."""
        if self._page is not None and not self._page.is_closed():
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None


plugin = MoflixPlugin()
