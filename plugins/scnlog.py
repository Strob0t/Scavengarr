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
from urllib.parse import quote_plus, urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    is_series_title,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["scnlog.me"]
_MAX_PAGES = 34

# Section of a release (first path segment of its URL) -> Torznab category.
# foreign/ holds films (2010) and episodes (5020) of every non-English
# language, German releases included.
_SECTION_CATEGORIES: dict[str, int] = {
    "movies": 2000,
    "tv-shows": 5000,
    "games": 4050,
    "apps": 4000,
    "pda": 4040,
    "music": 3000,
    "ebooks": 7000,
    "xxx": 6000,
}
# The labels ``_row_category()`` gives
_CATEGORIES = (*_SECTION_CATEGORIES.values(), 2010, 5020)
# The sections holding a category (for every result of ``served_category()``)
_SEARCH_PATHS: dict[int, tuple[str, ...]] = {
    2000: ("movies/", "foreign/"),
    2010: ("foreign/",),
    5000: ("tv-shows/", "foreign/"),
    5020: ("foreign/",),
    4000: ("apps/", "games/", "pda/"),
    4040: ("pda/",),
    4050: ("games/",),
    3000: ("music/",),
    7000: ("ebooks/",),
    6000: ("xxx/",),
}


def _row_category(row: dict[str, str]) -> int:
    """Torznab category of a search row, from its section and release name."""
    section = urlparse(urljoin("https://scnlog.me", row["detail_url"])).path
    section = section.strip("/").split("/")[0]
    if section == "foreign":
        return 5020 if is_series_title(row["title"]) else 2010
    return _SECTION_CATEGORIES.get(section, 8000)


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------
class _SearchResultParser:
    """Parse scnlog.me search results page (2026 layout, selectolax).

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
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for link in tree.css("a.next"):
            self.next_page_url = link.attributes.get("href") or self.next_page_url
        for box in tree.css("div.title"):
            # A title div inside another one is part of the outer one
            if any(p.tag == "div" and "title" in classes(p) for p in ancestors(box)):
                continue
            # The title is the text of its links, the URL the last link's
            links = [
                a
                for a in box.css("a")
                if a.attributes.get("href") and "next" not in classes(a)
            ]
            title = "".join(a.text() for a in links).strip()
            href = (links[-1].attributes.get("href") or "").strip() if links else ""
            if title and href:
                self.results.append({"title": title, "detail_url": href})


class _DetailPageParser:
    """Parse scnlog.me detail page for download links (2026 layout, selectolax).

    Structure::

        <h1 class="single-title">Release.Name</h1>
        <div class="download">
          <p><a href="https://nitroflare.com/view/...">https://nitroflare...</a></p>
          ...
        </div>

    The title is the first ``h1.single-title`` with text. Link texts are the
    URLs themselves, so the hoster name is taken from the link's domain.
    """

    def __init__(self) -> None:
        self.title: str = ""
        self.links: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for h1 in tree.css("h1.single-title"):
            self.title = h1.text().strip()
            if self.title:
                break
        for link in tree.css("div.download a"):
            href = (link.attributes.get("href") or "").strip()
            if href.startswith("http"):
                self.links.append({"hoster": _hoster_name(href), "link": href})


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

        parser = await parse_page(_SearchResultParser(), resp.text)

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

        parser = await parse_page(_DetailPageParser(), resp.text)
        return parser.title.strip(), parser.links

    async def _paginate_search(
        self,
        query: str,
        category_path: str,
        category: int | None,
    ) -> list[dict[str, str]]:
        """Collect the search rows of *category* over the result pages."""
        rows: list[dict[str, str]] = []
        next_url: str | None = None
        for _ in range(_MAX_PAGES):
            page_rows, next_url = await self._search_page(
                query, category_path, next_url
            )
            rows.extend(
                r for r in page_rows if category_matches(category, _row_category(r))
            )
            if not page_rows or not next_url or len(rows) >= self.effective_max_results:
                break
        return rows[: self.effective_max_results]

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
        if category is not None:
            category = served_category(category, _CATEGORIES)
            if category is None:
                return []  # no section of the site has this category
        await self._ensure_client()
        await self._verify_domain()

        paths = _SEARCH_PATHS[category] if category is not None else ("",)
        found = await asyncio.gather(
            *(self._paginate_search(query, path, category) for path in paths)
        )
        # One release per detail URL (sections do not overlap, but be safe)
        unique = {row["detail_url"]: row for rows in found for row in rows}
        all_items = list(unique.values())[: self.effective_max_results]
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
                    category=_row_category(item),
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
