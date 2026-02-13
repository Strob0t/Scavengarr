"""scnlog.me Python plugin for Scavengarr.

Scrapes scnlog.me (scene release log) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Two-stage scraping: search page -> detail page
- Search via GET /{category_path}?s={query}
- Category mapping: movies, tv-shows, games, music, ebooks, xxx
- Pagination up to 34 pages via "Next" link detection
- Detail page: extract download links from div.download a.external
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["scnlog.me"]
_MAX_PAGES = 34

# Torznab category -> scnlog URL path segment
_CATEGORY_MAP: dict[int, str] = {
    2000: "movies/",
    5000: "tv-shows/",
    4000: "games/",
    3000: "music/",
    7000: "ebooks/",
    6000: "xxx/",
}


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse scnlog.me search results page.

    Each result has structure::

        <div class="hentry">
          <div class="title">
            <h1><a href="/detail-url/">Title</a></h1>
          </div>
          ...
        </div>

    Pagination is detected via ``<div class="nav"><a>Next</a></div>``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

        # State tracking
        self._in_hentry = False
        self._hentry_depth = 0
        self._in_title_div = False
        self._in_h1 = False
        self._in_a = False
        self._in_nav = False
        self._in_nav_a = False
        self._nav_a_href = ""
        self._nav_a_text = ""

        # Current result
        self._current_title = ""
        self._current_href = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        # Result container: <div class="hentry">
        if tag == "div" and "hentry" in classes:
            self._in_hentry = True
            self._hentry_depth = 0
            self._current_title = ""
            self._current_href = ""
        elif tag == "div" and self._in_hentry:
            self._hentry_depth += 1
            if "title" in classes:
                self._in_title_div = True

        # Navigation: <div class="nav">
        if tag == "div" and "nav" in classes:
            self._in_nav = True

        if tag == "a" and self._in_nav:
            self._in_nav_a = True
            self._nav_a_href = attr_dict.get("href", "") or ""
            self._nav_a_text = ""

        if tag == "h1" and self._in_title_div:
            self._in_h1 = True

        if tag == "a" and self._in_h1:
            self._in_a = True
            self._current_href = attr_dict.get("href", "") or ""
            self._current_title = ""

    def handle_data(self, data: str) -> None:
        if self._in_a and self._in_h1:
            self._current_title += data
        if self._in_nav_a:
            self._nav_a_text += data

    def _handle_a_end(self) -> None:
        if self._in_a:
            self._in_a = False
        if self._in_nav_a:
            self._in_nav_a = False
            if "next" in self._nav_a_text.strip().lower():
                self.next_page_url = self._nav_a_href

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._handle_a_end()
        elif tag == "h1" and self._in_h1:
            self._in_h1 = False
        elif tag == "div":
            if self._in_nav:
                self._in_nav = False
            if self._in_hentry:
                if self._hentry_depth > 0:
                    self._hentry_depth -= 1
                    if self._in_title_div:
                        self._in_title_div = False
                else:
                    # End of hentry
                    self._in_hentry = False
                    self._in_title_div = False
                    title = self._current_title.strip()
                    href = self._current_href.strip()
                    if title and href:
                        self.results.append({"title": title, "detail_url": href})


class _DetailPageParser(HTMLParser):
    """Parse scnlog.me detail page for download links.

    Structure::

        <div class="title"><h1>Title</h1></div>
        <div class="download">
          <p><a class="external" href="https://host.com/file">Hoster</a></p>
          ...
        </div>

    Extracts title and download links (href + text as hoster name).
    """

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self.links: list[dict[str, str]] = []

        # State tracking
        self._in_title_div = False
        self._in_h1 = False
        self._in_download_div = False
        self._download_depth = 0
        self._in_external_a = False
        self._current_hoster = ""
        self._current_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        if tag == "div":
            if "title" in classes and not self._in_download_div:
                self._in_title_div = True
            elif "download" in classes:
                self._in_download_div = True
                self._download_depth = 0
            elif self._in_download_div:
                self._download_depth += 1

        if tag == "h1" and self._in_title_div:
            self._in_h1 = True
            self.title = ""

        if tag == "a" and self._in_download_div and "external" in classes:
            self._in_external_a = True
            self._current_href = attr_dict.get("href", "") or ""
            self._current_hoster = ""

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self.title += data
        if self._in_external_a:
            self._current_hoster += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self._in_title_div = False
        elif tag == "a" and self._in_external_a:
            self._in_external_a = False
            href = self._current_href.strip()
            hoster = self._current_hoster.strip()
            if href:
                self.links.append({"hoster": hoster or "unknown", "link": href})
        elif tag == "div" and self._in_download_div:
            if self._download_depth > 0:
                self._download_depth -= 1
            else:
                self._in_download_div = False


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class ScnlogPlugin(HttpxPluginBase):
    """Python plugin for scnlog.me using httpx."""

    name = "scnlog"
    provides = "download"
    _domains = _DOMAINS

    categories: dict[int, str] = {
        2000: "Movies",
        5000: "TV",
        4000: "Games",
        3000: "Music",
        7000: "E-Books",
        6000: "XXX",
    }

    async def _search_page(
        self,
        query: str,
        category_path: str,
        page_url: str | None = None,
    ) -> tuple[list[dict[str, str]], str]:
        """Fetch one search results page.

        Returns ``(results, next_page_url)``.
        """
        if page_url is None:
            encoded = quote_plus(query)
            page_url = f"{self.base_url}/{category_path}?s={encoded}"

        resp = await self._safe_fetch(page_url, context="search_page")
        if resp is None:
            return [], ""

        parser = _SearchResultParser()
        parser.feed(resp.text)

        next_url = ""
        if parser.next_page_url:
            next_url = urljoin(self.base_url, parser.next_page_url)

        self._log.info(
            "scnlog_search_page",
            url=page_url,
            count=len(parser.results),
            has_next=bool(next_url),
        )
        return parser.results, next_url

    async def _scrape_detail(self, detail_url: str) -> tuple[str, list[dict[str, str]]]:
        """Scrape a detail page for title and download links.

        Returns ``(title, links)`` where links is a list of
        ``{"hoster": ..., "link": ...}`` dicts.
        """
        resp = await self._safe_fetch(detail_url, context="detail_page")
        if resp is None:
            return "", []

        parser = _DetailPageParser()
        parser.feed(resp.text)
        return parser.title.strip(), parser.links

    async def _paginate_search(
        self,
        query: str,
        category_path: str,
    ) -> list[dict[str, str]]:
        """Paginate through search result pages and collect detail items."""
        first_results, next_url = await self._search_page(query, category_path)
        all_items = list(first_results)

        if not all_items and not next_url:
            return []

        pages_fetched = 1
        while (
            next_url
            and len(all_items) < self.effective_max_results
            and pages_fetched < _MAX_PAGES
        ):
            page_results, next_url = await self._search_page(
                query, category_path, next_url
            )
            if not page_results:
                break
            all_items.extend(page_results)
            pages_fetched += 1

        return all_items[: self.effective_max_results]

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search scnlog.me and return results with download links.

        Stage 1: Search pages with pagination for detail page URLs.
        Stage 2: Detail pages for download links (bounded concurrency).
        """
        await self._ensure_client()
        await self._verify_domain()

        category_path = _CATEGORY_MAP.get(category, "") if category else ""
        all_items = await self._paginate_search(query, category_path)
        if not all_items:
            return []

        # Scrape detail pages in parallel with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded_detail(
            item: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                detail_url = urljoin(self.base_url, item["detail_url"])
                title, links = await self._scrape_detail(detail_url)
                if not links:
                    return None
                return SearchResult(
                    title=title or item["title"],
                    download_link=links[0]["link"],
                    download_links=links,
                    source_url=detail_url,
                    category=category if category else 2000,
                )

        raw = await asyncio.gather(
            *[_bounded_detail(item) for item in all_items],
            return_exceptions=True,
        )

        results: list[SearchResult] = []
        for r in raw:
            if isinstance(r, SearchResult):
                results.append(r)
            elif isinstance(r, Exception):
                self._log.warning("scnlog_detail_error", error=str(r))

        return results


plugin = ScnlogPlugin()
