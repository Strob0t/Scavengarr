"""movieblog.to Python plugin for Scavengarr.

Scrapes movieblog.to (German DDL blog, WordPress-based) with:
- httpx for all requests (server-rendered HTML)
- WordPress search via /?s={query}
- Pagination via /page/{N}/?s={query} (~10 results/page, up to 100 pages)
- Two-stage: search results page gives titles + detail URLs,
  detail pages contain filecrypt.cc download containers
- Category detection from category tag links in search results
  ("Serie" → TV 5000, everything else → Movie 2000)
- Download links: filecrypt.cc containers (rapidgator, ddownload, nitroflare)

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import quote_plus

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.constants import (
    is_movie_category,
    is_tv_category,
)
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["movieblog.to"]
_MAX_PAGES = 100  # ~10 results/page → 100 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FILECRYPT_RE = re.compile(r"https?://(?:www\.)?filecrypt\.cc/Container/\w+\.html")
_SIZE_RE = re.compile(
    r"Größe:\s*([\d.,]+\s*(?:[KMGT]i?)?B)",
    re.IGNORECASE,
)

# WordPress category slugs that indicate TV content.
_TV_SLUGS = frozenset({"serie"})

# Hoster label detection from link text.
_HOSTER_MAP: dict[str, str] = {
    "rapidgator": "rapidgator",
    "ddownload": "ddownload",
    "nitroflare": "nitroflare",
    "1fichier": "1fichier",
    "turbobit": "turbobit",
}


# ---------------------------------------------------------------------------
# Search result parser (search listing page)
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse movieblog.to search results.

    Structure per result::

        <div class="post">
          <div class="post-date">
            <span class="post-month">Feb.</span>
            <span class="post-day">07</span>
          </div>
          <h1 id="post-132474">
            <a href="URL" title="...">TITLE</a>
          </h1>
          ...
          <p class="info_x">Thema:
            <a href="/category/drama/" rel="category tag">Drama</a>,
            <a href="/category/serie/" rel="category tag">Serie</a>
            ...
          </p>
        </div>
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str | int]] = []

        # Post tracking
        self._in_post = False
        self._post_depth = 0

        # Title tracking (h1 inside post)
        self._in_h1 = False
        self._in_title_link = False
        self._current_url = ""
        self._current_title = ""

        # Category tracking (info_x paragraph)
        self._in_info_x = False
        self._info_x_depth = 0
        self._in_cat_link = False
        self._cat_href = ""
        self._categories: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        # Track div.post containers
        if tag == "div":
            if "post" == classes.strip():
                self._in_post = True
                self._post_depth = 0
                self._current_url = ""
                self._current_title = ""
                self._categories = []
            elif self._in_post:
                self._post_depth += 1

        # h1 title inside post
        if tag == "h1" and self._in_post:
            self._in_h1 = True

        # Link inside h1 = title link
        if tag == "a" and self._in_h1:
            href = attr_dict.get("href", "") or ""
            if href:
                self._current_url = href
                self._in_title_link = True
                self._current_title = ""

        # p.info_x = category info
        if tag == "p" and "info_x" in classes:
            self._in_info_x = True
            self._info_x_depth = 0

        # Category tag links inside info_x
        if tag == "a" and self._in_info_x:
            rel = attr_dict.get("rel", "") or ""
            href = attr_dict.get("href", "") or ""
            if "category" in rel and "tag" in rel:
                self._in_cat_link = True
                self._cat_href = href

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_link:
            self._in_title_link = False

        if tag == "a" and self._in_cat_link:
            self._in_cat_link = False
            self._categories.append(self._cat_href)
            self._cat_href = ""

        if tag == "h1" and self._in_h1:
            self._in_h1 = False

        if tag == "p" and self._in_info_x:
            self._in_info_x = False

        if tag == "div" and self._in_post:
            if self._post_depth > 0:
                self._post_depth -= 1
            else:
                # End of post div → emit result
                self._in_post = False
                if self._current_title.strip() and self._current_url:
                    category = _detect_category(self._categories)
                    self.results.append(
                        {
                            "title": self._current_title.strip(),
                            "url": self._current_url,
                            "category": category,
                        }
                    )


def _detect_category(cat_hrefs: list[str]) -> int:
    """Map WordPress category hrefs to Torznab category IDs."""
    for href in cat_hrefs:
        slug = href.rstrip("/").rsplit("/", 1)[-1].lower()
        if slug in _TV_SLUGS:
            return 5000
    return 2000


# ---------------------------------------------------------------------------
# Pagination parser
# ---------------------------------------------------------------------------
class _PaginationParser(HTMLParser):
    """Extract next page URL from navigation_x div.

    Structure::

        <div class="navigation_x">
          <div class="alignleft">
            <a href="/page/2/?s=query">« vorherige Beiträge</a>
          </div>
          <div class="alignright">
            <a href="/page/2/?s=query">Nächste Seite »</a>
          </div>
        </div>
    """

    def __init__(self) -> None:
        super().__init__()
        self.next_page_url: str = ""

        self._in_nav = False
        self._nav_depth = 0
        self._in_right = False
        self._right_depth = 0
        self._capture_href = False
        self._last_href = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        if tag == "div":
            if "navigation_x" in classes:
                self._in_nav = True
                self._nav_depth = 0
            elif self._in_nav:
                self._nav_depth += 1
                if "alignright" in classes:
                    self._in_right = True
                    self._right_depth = 0

        # Capture last link href inside alignright
        if tag == "a" and self._in_right:
            href = attr_dict.get("href", "") or ""
            if href and "Seite" not in self.next_page_url:
                self._last_href = href
                self._capture_href = True

    def handle_data(self, data: str) -> None:
        if self._capture_href and "Nächste Seite" in data:
            self.next_page_url = self._last_href
        self._capture_href = False

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_nav:
            if self._in_right:
                if self._right_depth > 0:
                    self._right_depth -= 1
                else:
                    self._in_right = False
            if self._nav_depth > 0:
                self._nav_depth -= 1
            else:
                self._in_nav = False


# ---------------------------------------------------------------------------
# Detail page parser
# ---------------------------------------------------------------------------
class _DetailPageParser(HTMLParser):
    """Parse movieblog.to detail page for download links and metadata.

    Download structure::

        <strong>Download: </strong>
        <a href="https://filecrypt.cc/Container/XXX.html">Rapidgator.net</a>
        ...
        <strong>Mirror #1: </strong>
        <a href="https://filecrypt.cc/Container/YYY.html">Ddownload.com</a>

    Size structure::

        <strong>Größe: </strong>7,72 GB
    """

    def __init__(self) -> None:
        super().__init__()
        self.download_links: list[dict[str, str]] = []

        self._in_a = False
        self._a_href = ""
        self._a_text = ""
        self._all_text = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "a":
            attr_dict = dict(attrs)
            href = attr_dict.get("href", "") or ""
            if href and "filecrypt.cc/Container/" in href:
                self._in_a = True
                self._a_href = href
                self._a_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._a_text += data
        self._all_text += " " + data

    def _finish_link(self) -> None:
        """Store a completed filecrypt link with hoster label."""
        href = self._a_href
        text = self._a_text.strip().lower()
        if not _FILECRYPT_RE.match(href):
            return
        hoster = _detect_hoster(text)
        if not any(d["link"] == href for d in self.download_links):
            self.download_links.append({"hoster": hoster, "link": href})

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            self._in_a = False
            self._finish_link()

    def extract_size(self) -> str:
        """Extract file size from raw HTML text."""
        m = _SIZE_RE.search(self._all_text)
        if m:
            return m.group(1).strip()
        return ""


def _detect_hoster(text: str) -> str:
    """Detect hoster name from link text."""
    for keyword, label in _HOSTER_MAP.items():
        if keyword in text:
            return label
    return "filecrypt"


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class MovieblogPlugin(HttpxPluginBase):
    """movieblog.to httpx plugin."""

    name = "movieblog"
    provides = "download"
    _domains = _DOMAINS
    _max_concurrent = 3

    # ------------------------------------------------------------------
    # Search page fetching
    # ------------------------------------------------------------------

    async def _search_page(
        self,
        query: str,
        page: int,
    ) -> tuple[list[dict[str, str | int]], str]:
        """Fetch one page, return (results, next_page_url)."""
        if page == 1:
            url = f"{self.base_url}/?s={quote_plus(query)}"
        else:
            url = f"{self.base_url}/page/{page}/?s={quote_plus(query)}"

        resp = await self._safe_fetch(url)
        if resp is None or resp.status_code != 200:
            return [], ""

        html = resp.text
        parser = _SearchResultParser()
        parser.feed(html)

        pag_parser = _PaginationParser()
        pag_parser.feed(html)

        self._log.info(
            "movieblog_search_page",
            query=query,
            page=page,
            results=len(parser.results),
            has_next=bool(pag_parser.next_page_url),
        )
        return parser.results, pag_parser.next_page_url

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str | int]]:
        """Paginate through search results up to effective_max_results."""
        all_results: list[dict[str, str | int]] = []

        first_page, next_url = await self._search_page(query, 1)
        if not first_page:
            return []
        all_results.extend(first_page)

        page = 2
        while (
            next_url
            and page <= _MAX_PAGES
            and len(all_results) < self.effective_max_results
        ):
            page_results, next_url = await self._search_page(query, page)
            if not page_results:
                break
            all_results.extend(page_results)
            page += 1

        return all_results[: self.effective_max_results]

    # ------------------------------------------------------------------
    # Detail page scraping
    # ------------------------------------------------------------------

    async def _scrape_detail(
        self,
        item: dict[str, str | int],
    ) -> dict[str, str | int] | None:
        """Scrape a single detail page for download links."""
        url = str(item.get("url", ""))
        if not url:
            return None

        resp = await self._safe_fetch(url)
        if resp is None or resp.status_code != 200:
            self._log.warning("movieblog_detail_failed", url=url)
            return None

        parser = _DetailPageParser()
        parser.feed(resp.text)

        if not parser.download_links:
            return None

        item = dict(item)
        item["download_links"] = parser.download_links  # type: ignore[assignment]
        size = parser.extract_size()
        if size:
            item["size"] = size
        return item

    async def _scrape_details_parallel(
        self,
        items: list[dict[str, str | int]],
    ) -> list[dict[str, str | int]]:
        """Scrape detail pages in parallel with bounded concurrency."""
        sem = self._new_semaphore()

        async def _bounded(item: dict[str, str | int]) -> dict[str, str | int] | None:
            async with sem:
                return await self._scrape_detail(item)

        tasks = [_bounded(item) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched: list[dict[str, str | int]] = []
        for r in results:
            if isinstance(r, dict):
                enriched.append(r)
        return enriched

    # ------------------------------------------------------------------
    # Result conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _item_to_result(item: dict) -> SearchResult | None:
        """Convert an enriched item dict to a SearchResult."""
        title = str(item.get("title", "")).strip()
        links = item.get("download_links", [])
        if not title or not links:
            return None

        return SearchResult(
            title=title,
            download_link=links[0]["link"],
            download_links=links,
            source_url=str(item.get("url", "")),
            category=int(item.get("category", 2000)),
            size=str(item.get("size", "")) or None,
            release_name=title,
        )

    # ------------------------------------------------------------------
    # Category filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_by_category(
        items: list[dict[str, str | int]],
        category: int | None,
        season: int | None,
    ) -> list[dict[str, str | int]]:
        """Filter items by Torznab category and season hint."""
        if category is not None:
            filtered: list[dict[str, str | int]] = []
            for item in items:
                cat = int(item.get("category", 2000))
                if is_tv_category(category) and cat < 5000:
                    continue
                if is_movie_category(category) and cat >= 5000:
                    continue
                filtered.append(item)
            items = filtered

        if season is not None and category is None:
            items = [item for item in items if int(item.get("category", 2000)) >= 5000]
        return items

    # ------------------------------------------------------------------
    # Main search entry point
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search movieblog.to and return results with download links."""
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        all_items = self._filter_by_category(all_items, category, season)
        if not all_items:
            return []

        enriched = await self._scrape_details_parallel(all_items)

        results: list[SearchResult] = []
        for item in enriched:
            sr = self._item_to_result(item)
            if sr is not None:
                results.append(sr)

        return results


plugin = MovieblogPlugin()
