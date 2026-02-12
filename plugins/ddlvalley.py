"""ddlvalley.me Python plugin for Scavengarr.

Scrapes ddlvalley.me (WordPress DDL blog) with:
- Playwright for Cloudflare Turnstile bypass
- WordPress search via /?s=query
- Category filtering via /category/xxx/?s=query URL prefix
- Download link extraction from detail pages (direct hoster links)
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

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

_BASE_URL = "https://www.ddlvalley.me"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_PAGES = 3
_MAX_RESULTS = 1000

# Torznab category -> URL path segment mapping.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "category/movies",
    5000: "category/tv-shows",
    4000: "category/games",
    5020: "category/apps",
    3000: "category/music",
    7000: "category/reading",
}

# Known file hoster domains for download link detection.
_HOSTER_DOMAINS: set[str] = {
    "rapidgator.net",
    "rg.to",
    "uploaded.net",
    "uploaded.to",
    "ul.to",
    "go4up.com",
    "nitroflare.com",
    "nitro.download",
    "ddownload.com",
    "1fichier.com",
    "katfile.com",
    "turbobit.net",
    "filefactory.com",
    "hexupload.net",
    "filestore.me",
    "uptobox.com",
    "clicknupload.click",
    "clicknupload.co",
}


class _SearchResultParser(HTMLParser):
    """Extract post links from WordPress search/listing pages.

    Finds <h2><a href="/slug/">Title</a></h2> patterns that link
    to detail pages on the same domain.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.posts: list[dict[str, str]] = []
        self._base_url = base_url
        self._in_h2 = False
        self._in_a = False
        self._current_href = ""
        self._current_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2:
            attr_dict = dict(attrs)
            href = attr_dict.get("href", "")
            if href:
                self._in_a = True
                self._current_href = href
                self._current_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            self._in_a = False
            href = self._current_href
            title = self._current_text.strip()
            if href and title:
                url = urljoin(self._base_url, href)
                # Only accept links to our own domain
                if url.startswith(self._base_url) and url not in {
                    p["url"] for p in self.posts
                }:
                    self.posts.append({"title": title, "url": url})
        if tag == "h2":
            self._in_h2 = False


class _DetailPageParser(HTMLParser):
    """Extract download links from DDLValley detail page.

    Inside ``<div class="cont ...">`` looks for ``<a href>`` links
    pointing to known file hoster domains.  Tracks the current hoster
    group from preceding ``<strong>`` tags.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._in_cont = False
        self._div_depth = 0
        self._current_hoster = ""
        self._in_strong = False
        self._strong_text = ""
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "div":
            if self._in_cont:
                self._div_depth += 1
            else:
                classes = (attr_dict.get("class", "") or "").split()
                if "cont" in classes:
                    self._in_cont = True
                    self._div_depth = 0

        if tag == "strong" and self._in_cont:
            self._in_strong = True
            self._strong_text = ""

        if tag == "a" and self._in_cont:
            href = attr_dict.get("href", "")
            if href and href.startswith("http"):
                host = (urlparse(href).hostname or "").replace("www.", "")
                if _is_hoster_domain(host):
                    hoster = self._current_hoster or _hoster_from_domain(host)
                    if href not in self._seen_urls:
                        self._seen_urls.add(href)
                        self.links.append({"hoster": hoster, "link": href})

    def handle_data(self, data: str) -> None:
        if self._in_strong:
            self._strong_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_cont:
            if self._div_depth > 0:
                self._div_depth -= 1
            else:
                self._in_cont = False

        if tag == "strong" and self._in_strong:
            self._in_strong = False
            text = self._strong_text.strip().lower()
            if text:
                self._current_hoster = text


class _TitleParser(HTMLParser):
    """Extract page title from ``<title>`` tag, stripping site suffix."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True

    def handle_data(self, data: str) -> None:
        if self._in_title and self.title is None:
            text = data.strip()
            if text:
                text = re.sub(r"\s*\|\s*DDLValley.*$", "", text)
                if text:
                    self.title = text

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False


class DDLValleyPlugin:
    """Python plugin for ddlvalley.me using Playwright."""

    name = "ddlvalley"
    version = "1.0.0"
    mode = "playwright"
    provides = "download"
    default_language = "en"

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

    async def _search_posts(
        self,
        query: str,
        category_path: str = "",
        page_num: int = 1,
    ) -> list[dict[str, str]]:
        """Search DDLValley and return post URLs with titles.

        WordPress pagination: ``/page/N/?s=query`` for page >= 2.
        """
        assert self._context is not None  # noqa: S101

        if category_path:
            base = f"{self.base_url}/{category_path}"
        else:
            base = self.base_url

        if page_num > 1:
            url = f"{base}/page/{page_num}/?s={query}"
        else:
            url = f"{base}/?s={query}"

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

            log.info(
                "ddlvalley_search_results",
                query=query,
                category_path=category_path,
                page=page_num,
                count=len(parser.posts),
            )
            return parser.posts
        finally:
            if not page.is_closed():
                await page.close()

    async def _scrape_detail(self, post: dict[str, str]) -> SearchResult | None:
        """Scrape a detail page for download links."""
        assert self._context is not None  # noqa: S101

        page = await self._context.new_page()
        try:
            await page.goto(post["url"], wait_until="domcontentloaded")
            await self._wait_for_cloudflare(page)

            html = await page.content()
        except Exception:  # noqa: BLE001
            log.warning("ddlvalley_detail_fetch_failed", url=post["url"])
            return None
        finally:
            if not page.is_closed():
                await page.close()

        # Extract title from <title> tag (more reliable than search page)
        title_parser = _TitleParser()
        title_parser.feed(html)
        title = title_parser.title or post.get("title", "Unknown")

        # Extract download links
        link_parser = _DetailPageParser()
        link_parser.feed(html)

        if not link_parser.links:
            log.debug("ddlvalley_no_links", url=post["url"], title=title)
            return None

        primary_link = link_parser.links[0]["link"]

        return SearchResult(
            title=title,
            download_link=primary_link,
            download_links=link_parser.links,
            source_url=post["url"],
            category=2000,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search ddlvalley.me and return results with download links.

        Paginates through WordPress search pages to collect up to
        1000 results before scraping detail pages.
        """
        await self._ensure_browser()

        category_path = _CATEGORY_PATH_MAP.get(category, "") if category else ""

        # Paginate search results (WordPress: ~10 posts/page)
        all_posts: list[dict[str, str]] = []
        page_num = 1
        while len(all_posts) < _MAX_RESULTS:
            posts = await self._search_posts(query, category_path, page_num)
            if not posts:
                break
            all_posts.extend(posts)
            page_num += 1

        if not all_posts:
            return []

        all_posts = all_posts[:_MAX_RESULTS]

        sem = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

        async def _bounded_scrape(
            post: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(post)

        results = await asyncio.gather(
            *[_bounded_scrape(p) for p in all_posts],
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


def _is_hoster_domain(host: str) -> bool:
    """Check if a hostname belongs to a known file hoster."""
    return any(host.endswith(h) for h in _HOSTER_DOMAINS)


def _hoster_from_domain(host: str) -> str:
    """Extract hoster name from domain."""
    parts = host.replace("www.", "").split(".")
    return parts[0] if parts else "unknown"


plugin = DDLValleyPlugin()
