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


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


class _SearchResultParser(HTMLParser):
    """Parse jjs.page WordPress search result pages.

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
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str | int]] = []

        # Article tracking
        self._in_article = False

        # Title tracking
        self._in_entry_title = False
        self._entry_title_depth = 0
        self._in_title_a = False
        self._title_text = ""
        self._title_url = ""

        # Post-meta tracking
        self._in_post_meta = False
        self._meta_text = ""
        self._in_meta_a = False
        self._meta_a_href = ""
        self._category_hrefs: list[str] = []

    def _reset_article(self) -> None:
        self._title_text = ""
        self._title_url = ""
        self._meta_text = ""
        self._category_hrefs = []

    def _detect_category(self) -> int:
        """Determine Torznab category from post-meta category links."""
        for href in self._category_hrefs:
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

    def _emit_article(self) -> None:
        title = self._title_text.strip()
        url = self._title_url.strip()
        if not title or not url:
            return

        category = self._detect_category()

        self.results.append(
            {
                "title": title,
                "url": url,
                "category": category,
            }
        )

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        # --- Article start ---
        if tag == "article":
            self._in_article = True
            self._reset_article()

        if not self._in_article:
            return

        # --- H2 entry-title ---
        if tag == "h2" and "entry-title" in classes:
            self._in_entry_title = True
            self._entry_title_depth = 0
        elif tag == "h2" and self._in_entry_title:
            self._entry_title_depth += 1

        # --- Title link inside h2 ---
        if tag == "a" and self._in_entry_title:
            href = attr_dict.get("href", "") or ""
            if href:
                self._in_title_a = True
                self._title_text = ""
                self._title_url = href

        # --- Post meta ---
        if tag == "p" and "post-meta" in classes:
            self._in_post_meta = True
            self._meta_text = ""

        # --- Category links in post-meta ---
        if tag == "a" and self._in_post_meta:
            href = attr_dict.get("href", "") or ""
            if href:
                self._in_meta_a = True
                self._meta_a_href = href
                self._category_hrefs.append(href)

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._title_text += data
        if self._in_post_meta:
            self._meta_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self._in_article:
            self._in_article = False
            self._emit_article()
            return

        if not self._in_article:
            return

        if tag == "h2" and self._in_entry_title:
            if self._entry_title_depth > 0:
                self._entry_title_depth -= 1
            else:
                self._in_entry_title = False

        if tag == "a":
            if self._in_title_a:
                self._in_title_a = False
            if self._in_meta_a:
                self._in_meta_a = False

        if tag == "p" and self._in_post_meta:
            self._in_post_meta = False


class _PaginationParser(HTMLParser):
    """Extract the last page number from wp-pagenavi pagination.

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
        super().__init__()
        self.last_page = 1
        self._in_pagenavi = False
        self._pagenavi_depth = 0
        self._in_page_element = False
        self._page_text = ""
        self._is_last_link = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "") or ""

        if tag == "div":
            if self._in_pagenavi:
                self._pagenavi_depth += 1
            elif "wp-pagenavi" in classes:
                self._in_pagenavi = True
                self._pagenavi_depth = 0

        if not self._in_pagenavi:
            return

        if tag in ("a", "span"):
            # Skip "next" and "prev" links, keep numbered pages
            is_nav = "nextpostslink" in classes or "previouspostslink" in classes
            is_extend = "extend" in classes
            self._is_last_link = "last" in classes
            if not is_nav and not is_extend:
                self._in_page_element = True
                self._page_text = ""

            # Also extract page number from href for "last" link
            if self._is_last_link:
                href = attr_dict.get("href", "") or ""
                m = re.search(r"/page/(\d+)/", href)
                if m:
                    page_num = int(m.group(1))
                    if page_num > self.last_page:
                        self.last_page = page_num

    def handle_data(self, data: str) -> None:
        if self._in_page_element:
            self._page_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "span") and self._in_page_element:
            self._in_page_element = False
            text = self._page_text.strip()
            if text.isdigit():
                page_num = int(text)
                if page_num > self.last_page:
                    self.last_page = page_num

        if tag == "div" and self._in_pagenavi:
            if self._pagenavi_depth > 0:
                self._pagenavi_depth -= 1
            else:
                self._in_pagenavi = False


class _DetailPageParser(HTMLParser):
    """Parse jjs.page detail page for download links and metadata.

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

    Size appears in NFO/description text.
    """

    def __init__(self) -> None:
        super().__init__()
        self.download_links: list[dict[str, str]] = []
        self._seen_links: set[str] = set()
        self.size: str = ""

        # DDL section tracking
        self._in_ddl_content = False
        self._ddl_content_depth = 0
        self._in_ddl_slot = False
        self._ddl_slot_depth = 0
        self._current_slot_id = ""

        # Link tracking
        self._in_a = False
        self._a_href = ""
        self._a_text = ""

        # Size tracking
        self._all_text = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        element_id = attr_dict.get("id", "") or ""

        if tag == "div":
            # DDLContent container
            if element_id == "DDLContent":
                self._in_ddl_content = True
                self._ddl_content_depth = 0
            elif self._in_ddl_content:
                self._ddl_content_depth += 1
                # DDL slot divs (DDL1st, DDL2nd, DDL3rd)
                if element_id.startswith("DDL") and element_id != "DDLSample":
                    self._in_ddl_slot = True
                    self._ddl_slot_depth = 0
                    self._current_slot_id = element_id
                elif self._in_ddl_slot:
                    self._ddl_slot_depth += 1

        # Links inside DDL slots
        if tag == "a" and self._in_ddl_slot:
            href = attr_dict.get("href", "") or ""
            if href and "filecrypt.cc" in href:
                self._in_a = True
                self._a_href = href
                self._a_text = ""

        # Links outside DDL for filecrypt (fallback)
        if tag == "a" and not self._in_ddl_slot:
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
        """Process a completed <a> tag and store the download link."""
        href = self._a_href
        text = self._a_text.strip().lower()
        if not (href and _FILECRYPT_RE.match(href)):
            return
        hoster = "filecrypt"
        if "ddownload" in text:
            hoster = "ddownload"
        elif "rapidgator" in text:
            hoster = "rapidgator"
        if href not in self._seen_links:
            self._seen_links.add(href)
            self.download_links.append({"hoster": hoster, "link": href})

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            self._in_a = False
            self._finish_link()

        if tag == "div":
            if self._in_ddl_slot:
                if self._ddl_slot_depth > 0:
                    self._ddl_slot_depth -= 1
                else:
                    self._in_ddl_slot = False
                    self._current_slot_id = ""
            elif self._in_ddl_content:
                if self._ddl_content_depth > 0:
                    self._ddl_content_depth -= 1
                else:
                    self._in_ddl_content = False

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

        parser = _SearchResultParser()
        parser.feed(html)

        pag_parser = _PaginationParser()
        pag_parser.feed(html)

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

        parser = _DetailPageParser()
        parser.feed(resp.text)

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

        return SearchResult(
            title=title,
            download_link=links[0]["link"],
            download_links=links,
            source_url=str(item.get("url", "")),
            category=int(item.get("category", 2000)),
            size=str(item.get("size", "")) or None,
            release_name=title,
        )

    @staticmethod
    def _filter_by_category(
        items: list[dict[str, str | int]],
        category: int | None,
        season: int | None,
    ) -> list[dict[str, str | int]]:
        """Filter search items by Torznab category and season hint."""
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

        # When season is requested but no category, restrict to TV
        if season is not None and category is None:
            items = [item for item in items if int(item.get("category", 2000)) >= 5000]
        return items

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search jjs.page and return results with download links."""
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


plugin = JjsPlugin()
