"""scnlog.me Python plugin for Scavengarr.

Scrapes scnlog.me (scene release log) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Two-stage scraping: search page -> detail page
- Search via GET /{category_path}?s={query}
- Category mapping: movies, tv-shows, games, music, ebooks, xxx
- Pagination up to 34 pages via "Next" link detection
- Detail page: title from h1.single-title, download links from div.download
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse

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
    """Parse scnlog.me search results page (2026 layout).

    Each result has structure::

        <li class="row has-cat">
          <div class="row-body">
            <div class="title">
              <a href="/detail-url/"><span class="title-start">Release</span></a>
            </div>
            ...
          </div>
        </li>

    Pagination: ``<a class="next pg" href="/page/2/?s=...">Next</a>``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

        self._title_div_depth = 0  # >0 while inside div.title
        self._in_a = False
        self._current_title = ""
        self._current_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        if tag == "div":
            if self._title_div_depth:
                self._title_div_depth += 1
            elif "title" in classes:
                self._title_div_depth = 1
                self._current_title = ""
                self._current_href = ""
        elif tag == "a":
            href = attr_dict.get("href", "") or ""
            if "next" in classes and href:
                self.next_page_url = href
            elif self._title_div_depth and href:
                self._in_a = True
                self._current_href = href

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_a = False
        elif tag == "div" and self._title_div_depth:
            self._title_div_depth -= 1
            if not self._title_div_depth:
                title = self._current_title.strip()
                href = self._current_href.strip()
                if title and href:
                    self.results.append({"title": title, "detail_url": href})


class _DetailPageParser(HTMLParser):
    """Parse scnlog.me detail page for download links (2026 layout).

    Structure::

        <h1 class="single-title">Release.Name</h1>
        <div class="download">
          <p><a href="https://nitroflare.com/view/...">https://nitroflare...</a></p>
          ...
        </div>

    Link texts are the URLs themselves, so the hoster name is taken from
    the link's domain.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self.links: list[dict[str, str]] = []

        self._in_h1 = False
        self._download_depth = 0  # >0 while inside div.download

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        if tag == "h1" and "single-title" in classes and not self.title:
            self._in_h1 = True
        elif tag == "div":
            if self._download_depth:
                self._download_depth += 1
            elif "download" in classes:
                self._download_depth = 1
        elif tag == "a" and self._download_depth:
            href = (attr_dict.get("href", "") or "").strip()
            if href.startswith("http"):
                self.links.append({"hoster": _hoster_name(href), "link": href})

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self.title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = self.title.strip()
        elif tag == "div" and self._download_depth:
            self._download_depth -= 1


def _hoster_name(url: str) -> str:
    """Second-level domain of *url* (``https://www.nitroflare.com/x`` -> nitroflare)."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host or "unknown"


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
