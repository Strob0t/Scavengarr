"""jjs.page Python plugin for Scavengarr.

Scrapes jjs.page (German DDL blog, WordPress-based) with:
- httpx for all requests (server-rendered HTML behind Cloudflare)
- WordPress search via /?s={query}
- Pagination via /page/{N}/?s={query} (~8-10 results/page, up to 125 pages)
- Two-stage: search results page gives titles + detail URLs,
  detail pages contain filecrypt.cc download containers
- Category detection from post-meta links (/jjmovies/, /jjseries/, /other/)
- Download links point to filecrypt.cc containers (ddownload, rapidgator)

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["jjs.page"]
_MAX_PAGES = 125  # ~8 results/page → 125 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Category URL path → Torznab category ID.
_CATEGORY_MAP: dict[str, int] = {
    "jjmovies": 2000,
    "jjseries": 5000,
    "other": 2000,  # scene/other defaults to movies
}

# Regex: 4-digit year in title (e.g. "Iron Man 2008 German ...")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Regex: season number in title (e.g. "S03" or "S03E05")
_SEASON_RE = re.compile(r"\bS(\d{2})", re.IGNORECASE)

# Regex: filecrypt container URL
_FILECRYPT_RE = re.compile(r"https?://filecrypt\.cc/Container/\w+\.html")

# Regex: size like "60.1 GB" or "6072 MB" or "1.2 GB"
_SIZE_RE = re.compile(r"([\d.,]+\s*(?:[KMGT]i?)?B)\b", re.IGNORECASE)

# Pagination classes of elements that name no page ("next", "prev", "...")
_NOT_PAGE_NUMBERS = frozenset({"nextpostslink", "previouspostslink", "extend"})


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


class _SearchResultParser:
    """Parse jjs.page WordPress search result pages (selectolax).

    Each result is an ``<article>`` with structure::

        <article class="post-type-post ... hentry">
          <h2 class="entry-title">
            <a href="https://jjs.page/slug/">Title Here</a>
          </h2>
          <p class="post-meta">
            19. Februar 2023 | 20:50 |
            <a href="https://jjs.page/jjmovies/">JJ Film Releases</a>,
            <a href="https://jjs.page/jjmovies/uhd-jjmovies/">UltraHD</a>
          </p>
        </article>

    The last link of the ``entry-title`` names the result, the links of the
    ``post-meta`` give its category.
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str | int]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for article in tree.css("article"):
            links = [
                link
                for link in article.css("h2.entry-title a")
                if link.attributes.get("href")
            ]
            if not links:
                continue
            title = links[-1].text().strip()
            url = (links[-1].attributes.get("href") or "").strip()
            if not title or not url:
                continue
            hrefs = [
                href
                for link in article.css("p.post-meta a")
                if (href := link.attributes.get("href") or "")
            ]
            self.results.append(
                {"title": title, "url": url, "category": self._detect_category(hrefs)}
            )

    @staticmethod
    def _detect_category(hrefs: list[str]) -> int:
        """Determine Torznab category from post-meta category links."""
        for href in hrefs:
            # e.g. "https://jjs.page/jjseries/" → "jjseries"
            # e.g. "https://jjs.page/jjmovies/hd-jjmovies/" → "jjmovies"
            path = href.rstrip("/").split("/")
            for segment in path:
                if segment in _CATEGORY_MAP:
                    return _CATEGORY_MAP[segment]
            # Check if any segment starts with a category key
            for segment in path:
                for key, cat_id in _CATEGORY_MAP.items():
                    if key in segment:
                        return cat_id
        return 2000  # default: movies


class _PaginationParser:
    """Extract the last page number from wp-pagenavi pagination (selectolax).

    Pagination structure::

        <div class="wp-pagenavi">
            <span class="current">1</span>
            <a href="/page/2/?s=...">2</a>
            <a href="/page/3/?s=...">3</a>
            ...
            <a class="last" href="/page/16/?s=...">Last »</a>
        </div>
    """

    def __init__(self) -> None:
        self.last_page = 1

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for node in tree.css("div.wp-pagenavi a, div.wp-pagenavi span"):
            names = classes(node)
            # Skip "next" and "prev" links, keep numbered pages
            if _NOT_PAGE_NUMBERS.isdisjoint(names):
                text = node.text().strip()
                if text.isdigit():
                    self.last_page = max(self.last_page, int(text))
            # Also extract page number from href for "last" link
            if "last" in names:
                m = re.search(r"/page/(\d+)/", node.attributes.get("href") or "")
                if m:
                    self.last_page = max(self.last_page, int(m.group(1)))


class _DetailPageParser:
    """Parse jjs.page detail page for download links and metadata (selectolax).

    Download section structure::

        <div id="DDLContent">
          <div id="DDL1st">
            <a href="https://filecrypt.cc/Container/ABC123.html">
              <img src="https://filecrypt.cc/Stat/...">
              Ddownload.com
            </a>
          </div>
          <div id="DDL2nd">
            <a href="https://filecrypt.cc/Container/DEF456.html">
              Rapidgator.net
            </a>
          </div>
        </div>

    Every filecrypt container link counts, inside the DDL section or not;
    its text names the hoster. Size appears in NFO/description text.
    """

    def __init__(self) -> None:
        self.download_links: list[dict[str, str]] = []
        self._seen_links: set[str] = set()
        self.size: str = ""
        # The page's text nodes, a space before each: extract_size() takes
        # the first size in it
        self._all_text = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for link in tree.css("a[href*='filecrypt.cc/Container/']"):
            self._add_link(link.attributes.get("href") or "", link.text())
        self._all_text += " " + tree.text(separator=" ")

    def _add_link(self, href: str, text: str) -> None:
        """Store a filecrypt container link, its hoster named by its text."""
        if not _FILECRYPT_RE.match(href):
            return
        text = text.strip().lower()
        hoster = "filecrypt"
        if "ddownload" in text:
            hoster = "ddownload"
        elif "rapidgator" in text:
            hoster = "rapidgator"
        if href not in self._seen_links:
            self._seen_links.add(href)
            self.download_links.append({"hoster": hoster, "link": href})

    def extract_size(self) -> str:
        """Extract file size from the page text."""
        if self.size:
            return self.size
        m = _SIZE_RE.search(self._all_text)
        if m:
            self.size = m.group(1).strip()
        return self.size


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class JjsPlugin(HttpxPluginBase):
    """Python plugin for jjs.page using httpx."""

    name = "jjs"
    provides = "download"
    _domains = _DOMAINS
    _max_concurrent = 3

    async def _search_page(
        self, query: str, page: int = 1
    ) -> tuple[list[dict[str, str | int]], int]:
        """Fetch one search results page and return (results, last_page)."""
        encoded = quote_plus(query)
        if page > 1:
            url = f"{self.base_url}/page/{page}/?s={encoded}"
        else:
            url = f"{self.base_url}/?s={encoded}"

        resp = await self._safe_fetch(url, context=f"search_page_{page}")
        if resp is None:
            return [], 1

        html = resp.text

        parser = await parse_page(_SearchResultParser(), html)

        pag_parser = await parse_page(_PaginationParser(), html)

        self._log.info(
            "jjs_search_page",
            query=query,
            page=page,
            results=len(parser.results),
            last_page=pag_parser.last_page,
        )
        return parser.results, pag_parser.last_page

    async def _search_all_pages(self, query: str) -> list[dict[str, str | int]]:
        """Paginate through search results up to effective_max_results."""
        all_results: list[dict[str, str | int]] = []

        first_page, last_page = await self._search_page(query, 1)
        if not first_page:
            return []
        all_results.extend(first_page)

        max_page = min(last_page, _MAX_PAGES)

        for page_num in range(2, max_page + 1):
            if len(all_results) >= self.effective_max_results:
                break
            results, _ = await self._search_page(query, page_num)
            if not results:
                break
            all_results.extend(results)

        return all_results[: self.effective_max_results]

    async def _scrape_detail(self, url: str) -> dict[str, object]:
        """Scrape a detail page for download links and size."""
        resp = await self._safe_fetch(url, context="detail_page")
        if resp is None:
            return {"download_links": [], "size": ""}

        parser = await parse_page(_DetailPageParser(), resp.text)

        return {
            "download_links": parser.download_links,
            "size": parser.extract_size(),
        }

    async def _scrape_details_parallel(
        self, items: list[dict[str, str | int]]
    ) -> list[dict[str, object]]:
        """Scrape detail pages in parallel with bounded concurrency."""
        sem = self._new_semaphore()

        async def _fetch_one(item: dict[str, str | int]) -> dict[str, object]:
            async with sem:
                detail = await self._scrape_detail(str(item["url"]))
                return {**item, **detail}

        tasks = [_fetch_one(item) for item in items]
        return list(await asyncio.gather(*tasks))

    @staticmethod
    def _item_to_result(item: dict[str, object]) -> SearchResult | None:
        """Convert a parsed item dict to a SearchResult."""
        links = item.get("download_links", [])
        if not isinstance(links, list) or not links:
            return None

        title = str(item.get("title", ""))
        if not title:
            return None
        category = item.get("category")

        return SearchResult(
            title=title,
            download_link=links[0]["link"],
            download_links=links,
            source_url=str(item.get("url", "")),
            category=category if isinstance(category, int) else 2000,
            size=str(item.get("size", "")) or None,
            release_name=title,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search jjs.page and return results with download links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, (2000, 5000))
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        all_items = [
            item
            for item in all_items
            if category_matches(category, int(item.get("category", 2000)))
        ]
        if not all_items:
            return []

        enriched = await self._scrape_details_parallel(all_items)

        results: list[SearchResult] = []
        for item in enriched:
            sr = self._item_to_result(item)
            if sr is not None:
                results.append(sr)

        return results


plugin = JjsPlugin()
