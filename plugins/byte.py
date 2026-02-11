"""byte.to Python plugin for Scavengarr.

Scrapes byte.to (German DDL site) with:
- Playwright for Cloudflare and iframe-based download links
- Advanced search via /?q=query&c=category_id&t=1
- Category filtering via dropdown category ID parameter
- Multi-page pagination (200 items per page, up to 5 pages)
- Download link extraction from iframes on detail pages
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

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

_BASE_URL = "https://byte.to"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_PAGES = 3
_MAX_PAGES = 5

# Torznab category → site category ID for search URL parameter ``c=``.
_TORZNAB_TO_SITE_CATEGORY: dict[int, str] = {
    2000: "1",  # Filme
    5000: "2",  # Tv
    4000: "15",  # Spiele
    5020: "29",  # Programme
    3000: "99",  # Musik
    7000: "41",  # Bücher
    6000: "46",  # XxX
}

# Site category name (lowercase) → Torznab category ID.
_SITE_CATEGORY_MAP: dict[str, int] = {
    # Filme (2000)
    "kinofilme": 2000,
    "sd - xvid": 2000,
    "sd - x264": 2000,
    "dvd": 2000,
    "microhd": 2000,
    "hd - 720p": 2000,
    "hd - 1080p": 2000,
    "uhd - 2160p": 2000,
    "filme": 2000,
    # TV (5000)
    "serien": 5000,
    "dokumentation": 5000,
    "tv": 5000,
    # Spiele (4000)
    "pc": 4000,
    "win": 4000,
    "konsolen": 4000,
    "spiele": 4000,
    # Programme (5020)
    "programme": 5020,
    # Musik (3000)
    "alben": 3000,
    "charts": 3000,
    "musik": 3000,
    # Bücher (7000)
    "ebooks": 7000,
    "comics": 7000,
    "bücher": 7000,
    # Hörbücher (7020)
    "hörbücher": 7020,
    # XxX (6000)
    "xxx": 6000,
}


class _SearchResultParser(HTMLParser):
    """Extract search results from byte.to search page.

    Parses ``<table class="SEARCH_ITEMLIST">`` for:
    - Title links inside ``<p class="TITLE"><a href="...">``
    - Category links with ``href="/?cat=N"``
    - Total hit count from ``<h1>Suche nach: ... (N Treffer)</h1>``
    - Pagination from ``<table class="NAVIGATION">``
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.total_hits: int = 0
        self.max_page: int = 1
        self._base_url = base_url

        # Title tracking
        self._in_title_p = False
        self._in_title_a = False
        self._current_href = ""
        self._current_title = ""

        # Pending result (title found, waiting for category)
        self._pending_url = ""
        self._pending_title = ""

        # Category tracking
        self._in_cat_a = False
        self._current_category = ""

        # Hit count
        self._in_h1 = False
        self._h1_text = ""

        # Navigation
        self._in_nav = False

    def _handle_a_start(self, attr_dict: dict[str, str | None]) -> None:
        href = attr_dict.get("href", "") or ""

        if self._in_title_p and href:
            self._in_title_a = True
            self._current_href = href
            self._current_title = ""
        elif self._in_nav and href and "start=" in href:
            m = re.search(r"start=(\d+)", href)
            if m:
                page_num = int(m.group(1))
                if page_num > self.max_page:
                    self.max_page = page_num
        elif href.startswith("/?cat=") and self._pending_url:
            self._in_cat_a = True
            self._current_category = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        if tag == "table":
            cls = (attr_dict.get("class", "") or "").upper()
            if cls == "NAVIGATION":
                self._in_nav = True

        if tag == "p":
            cls = (attr_dict.get("class", "") or "").upper()
            if cls == "TITLE":
                # Flush any pending result without category
                if self._pending_url:
                    self.results.append(
                        {
                            "title": self._pending_title,
                            "url": self._pending_url,
                            "category": "",
                        }
                    )
                    self._pending_url = ""
                self._in_title_p = True

        if tag == "a":
            self._handle_a_start(attr_dict)

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_title_a:
            self._current_title += data
        if self._in_cat_a:
            self._current_category += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            m = re.search(r"\((\d+)\s+Treffer\)", self._h1_text)
            if m:
                self.total_hits = int(m.group(1))

        if tag == "table" and self._in_nav:
            self._in_nav = False

        if tag == "a":
            if self._in_title_a:
                self._in_title_a = False
                title = self._current_title.strip()
                href = self._current_href
                if title and href:
                    url = urljoin(self._base_url, href)
                    self._pending_url = url
                    self._pending_title = title

            if self._in_cat_a:
                self._in_cat_a = False
                category = self._current_category.strip()
                if self._pending_url:
                    self.results.append(
                        {
                            "title": self._pending_title,
                            "url": self._pending_url,
                            "category": category,
                        }
                    )
                    self._pending_url = ""
                    self._pending_title = ""

        if tag == "p" and self._in_title_p:
            self._in_title_p = False

    def flush_pending(self) -> None:
        """Emit any pending result that has no category yet."""
        if self._pending_url:
            self.results.append(
                {
                    "title": self._pending_title,
                    "url": self._pending_url,
                    "category": "",
                }
            )
            self._pending_url = ""
            self._pending_title = ""


class _DetailPageParser(HTMLParser):
    """Extract metadata from byte.to detail page.

    Finds:
    - Release name: first ``<td>`` text matching scene-release pattern
    - Size: cell following a ``Größe`` label
    - Category: cell following a ``Kategorie`` label
    """

    def __init__(self) -> None:
        super().__init__()
        self.release_name: str = ""
        self.size: str = ""
        self.category: str = ""

        self._in_td = False
        self._td_text = ""
        self._prev_td_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self._in_td = True
            self._td_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._td_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag != "td" or not self._in_td:
            return

        self._in_td = False
        text = self._td_text.strip()
        prev = self._prev_td_text.lower()

        if text:
            # Check if previous cell was a label
            if "größe" in prev:
                self.size = text
            elif "kategorie" in prev:
                self.category = text

            # Detect release name: scene pattern (dots, no spaces, 3+ dots)
            if (
                not self.release_name
                and " " not in text
                and text.count(".") >= 3
                and len(text) > 15
                and not text.startswith("http")
            ):
                self.release_name = text

        self._prev_td_text = text


class _IframeLinkParser(HTMLParser):
    """Extract download links from byte.to iframe content.

    Looks for ``<a>`` links preceded by ``<img alt="hoster.domain">``
    or with link text matching ``Online hoster.domain``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._in_a = False
        self._current_href = ""
        self._current_text = ""
        self._current_img_alt = ""
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "img":
            alt = (attr_dict.get("alt", "") or "").strip()
            if alt and "." in alt:
                self._current_img_alt = alt

        if tag == "a":
            href = attr_dict.get("href", "") or ""
            if href and href.startswith("http"):
                self._in_a = True
                self._current_href = href
                self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_a:
            return

        self._in_a = False
        href = self._current_href
        text = self._current_text.strip().lower()

        if href in self._seen_urls:
            self._current_img_alt = ""
            return

        # Determine hoster name from img alt or link text
        hoster = ""
        if self._current_img_alt and "." in self._current_img_alt:
            hoster = self._current_img_alt.split(".")[0].lower()
        if not hoster:
            m = re.search(r"online\s+(\S+)", text)
            if m:
                domain = m.group(1).strip()
                hoster = domain.split(".")[0]

        if hoster:
            self._seen_urls.add(href)
            self.links.append({"hoster": hoster, "link": href})

        self._current_img_alt = ""


def _site_category_to_torznab(category_name: str) -> int:
    """Map site category name to Torznab category ID."""
    return _SITE_CATEGORY_MAP.get(category_name.lower().strip(), 2000)


class BytePlugin:
    """Python plugin for byte.to using Playwright."""

    name = "byte"
    version = "1.0.0"
    mode = "playwright"

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.base_url = _BASE_URL

    async def _ensure_browser(self) -> None:
        """Launch Chromium if not already running."""
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
        )
        self._context = await self._browser.new_context(
            user_agent=_USER_AGENT,
        )

    async def _wait_for_cloudflare(self, page: Page) -> None:
        """If Cloudflare challenge is detected, wait for it to resolve."""
        try:
            await page.wait_for_function(
                "() => !document.title.includes('Just a moment')",
                timeout=15_000,
            )
        except Exception:  # noqa: BLE001
            pass  # proceed anyway — page may still be usable

    async def _search_page(
        self,
        query: str,
        site_category: str = "",
        page_num: int = 1,
    ) -> tuple[list[dict[str, str]], int, int]:
        """Fetch a single search results page.

        Returns ``(results, total_hits, max_page)``.
        """
        assert self._context is not None  # noqa: S101

        url = f"{self.base_url}/?q={query}&t=1"
        if site_category:
            url += f"&c={site_category}"
        if page_num > 1:
            url += f"&h=1&e=0&start={page_num}"

        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await self._wait_for_cloudflare(page)

            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001
                pass

            html = await page.content()
            parser = _SearchResultParser(self.base_url)
            parser.feed(html)
            parser.flush_pending()

            log.info(
                "byte_search_page",
                query=query,
                page=page_num,
                results=len(parser.results),
                total_hits=parser.total_hits,
                max_page=parser.max_page,
            )
            return parser.results, parser.total_hits, parser.max_page
        finally:
            if not page.is_closed():
                await page.close()

    async def _extract_iframe_links(self, page: Page) -> list[dict[str, str]]:
        """Extract download links from all iframes on the page."""
        all_links: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                await frame.wait_for_load_state("domcontentloaded", timeout=5_000)
                content = await frame.content()

                parser = _IframeLinkParser()
                parser.feed(content)

                for link in parser.links:
                    if link["link"] not in seen_urls:
                        seen_urls.add(link["link"])
                        all_links.append(link)
            except Exception:  # noqa: BLE001
                continue

        return all_links

    async def _scrape_detail(self, result: dict[str, str]) -> SearchResult | None:
        """Scrape a detail page for metadata and download links."""
        assert self._context is not None  # noqa: S101

        page = await self._context.new_page()
        try:
            await page.goto(result["url"], wait_until="domcontentloaded")
            await self._wait_for_cloudflare(page)

            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:  # noqa: BLE001
                pass

            # Parse metadata from main page
            html = await page.content()
            detail_parser = _DetailPageParser()
            detail_parser.feed(html)

            # Extract links from iframes
            links = await self._extract_iframe_links(page)

            if not links:
                log.debug("byte_no_links", url=result["url"])
                return None

            title = detail_parser.release_name or result.get("title", "Unknown")
            category_name = detail_parser.category or result.get("category", "")
            torznab_cat = _site_category_to_torznab(category_name)

            return SearchResult(
                title=title,
                download_link=links[0]["link"],
                download_links=links,
                source_url=result["url"],
                size=detail_parser.size or None,
                category=torznab_cat,
            )
        except Exception:  # noqa: BLE001
            log.warning("byte_detail_fetch_failed", url=result["url"])
            return None
        finally:
            if not page.is_closed():
                await page.close()

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search byte.to and return results with download links."""
        await self._ensure_browser()

        site_category = _TORZNAB_TO_SITE_CATEGORY.get(category, "") if category else ""

        # Fetch first page
        first_results, total_hits, max_page = await self._search_page(
            query, site_category
        )

        all_results = list(first_results)

        # Fetch additional pages if needed
        pages_needed = min(max_page, _MAX_PAGES)
        for page_num in range(2, pages_needed + 1):
            more_results, _, _ = await self._search_page(query, site_category, page_num)
            all_results.extend(more_results)
            if not more_results:
                break

        if not all_results:
            return []

        # Scrape detail pages in parallel with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

        async def _bounded_scrape(
            r: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r)

        results = await asyncio.gather(
            *[_bounded_scrape(r) for r in all_results],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]

    async def cleanup(self) -> None:
        """Close browser and Playwright resources."""
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


plugin = BytePlugin()
