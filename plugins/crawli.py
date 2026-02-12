"""crawli.net Python plugin for Scavengarr.

Scrapes crawli.net (German download search engine) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Search via GET /{category}/{query}/ with spaces as +
- Category filtering via URL path segment (film, serie, spiel, music, apps)
- Pagination up to 1000 items (10 results/page, max 100 pages)
- Single-stage: title, source URL, date, description all on search page

Multi-domain support: crawli.net, www.crawli.net.
No authentication required.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote_plus

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["crawli.net", "www.crawli.net"]
_MAX_PAGES = 100  # 10 results/page -> 100 pages for 1000 items
_RESULTS_PER_PAGE = 10

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Torznab category -> crawli URL path segment.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "film",
    5000: "serie",
    4000: "spiel",
    3000: "music",
    5020: "apps",
}

# Reverse mapping: crawli path segment -> Torznab category ID.
_PATH_TO_TORZNAB: dict[str, int] = {
    "film": 2000,
    "serie": 5000,
    "spiel": 4000,
    "music": 3000,
    "apps": 5020,
}

# Date regex for parsing "29.01.2026 19:35" format.
_DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse crawli.net search results page.

    Each result has structure::

        <div class="entry-content sresd">
          <strong class="sres">
            <a href="http://crawli.net/go/?/ID/" class="sres3">Title</a>
          </strong>
          <div style="float:right"><em class="fnfo">Download</em></div>
          <div class="scont">
            <p>Description text...</p>
            <address class="resl author">source-url-here</address>
            <small class="rtime published">29.01.2026 19:35</small>
          </div>
        </div>

    Pagination is in ``#foot > span.pages > a`` with ``p-N`` links.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.max_page: int = 1

        # State tracking
        self._in_sres = False
        self._in_title_a = False
        self._in_scont = False
        self._scont_depth = 0
        self._in_address = False
        self._in_small = False
        self._in_p = False
        self._in_foot_pages = False

        # Current result data
        self._current_title = ""
        self._current_source_url = ""
        self._current_date = ""
        self._current_description = ""

    def _reset_result(self) -> None:
        self._current_title = ""
        self._current_source_url = ""
        self._current_date = ""
        self._current_description = ""

    def _emit_result(self) -> None:
        title = self._current_title.strip()
        source_url = self._current_source_url.strip()
        if title and source_url:
            # Ensure source URL has a scheme
            if not source_url.startswith("http"):
                source_url = f"https://{source_url}"
            self.results.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "date": self._current_date.strip(),
                    "description": self._current_description.strip(),
                }
            )
        self._reset_result()

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        # Detect result container: <div class="entry-content sresd">
        if tag == "div" and "entry-content" in classes and "sresd" in classes:
            self._reset_result()

        # Title strong: <strong class="sres">
        if tag == "strong" and "sres" in classes:
            self._in_sres = True

        # Title link: <a class="sres3">
        if tag == "a" and self._in_sres and "sres3" in classes:
            self._in_title_a = True
            self._current_title = ""

        # Content container: <div class="scont">
        if tag == "div" and "scont" in classes:
            self._in_scont = True
            self._scont_depth = 0
        elif tag == "div" and self._in_scont:
            self._scont_depth += 1

        # Source URL: <address class="resl author">
        if tag == "address" and "resl" in classes:
            self._in_address = True
            self._current_source_url = ""

        # Date: <small class="rtime published">
        if tag == "small" and "rtime" in classes:
            self._in_small = True
            self._current_date = ""

        # Description paragraph inside scont
        if tag == "p" and self._in_scont:
            self._in_p = True

        # Pagination: <span class="pages"> inside #foot
        if tag == "div" and attr_dict.get("id") == "foot":
            self._in_foot_pages = True

        # Pagination links: <a href="//crawli.net/{cat}/{query}/p-N/">
        if tag == "a" and self._in_foot_pages:
            href = attr_dict.get("href", "") or ""
            m = re.search(r"/p-(\d+)/", href)
            if m:
                page_num = int(m.group(1))
                if page_num > self.max_page:
                    self.max_page = page_num

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data

        if self._in_address:
            self._current_source_url += data

        if self._in_small:
            self._current_date += data

        if self._in_p and self._in_scont:
            self._current_description += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_a:
            self._in_title_a = False

        if tag == "strong" and self._in_sres:
            self._in_sres = False

        if tag == "address" and self._in_address:
            self._in_address = False

        if tag == "small" and self._in_small:
            self._in_small = False

        if tag == "p" and self._in_p:
            self._in_p = False

        if tag == "div" and self._in_scont:
            if self._scont_depth > 0:
                self._scont_depth -= 1
            else:
                self._in_scont = False
                # End of result entry — emit it
                self._emit_result()

        if tag == "div" and self._in_foot_pages:
            self._in_foot_pages = False


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class CrawliPlugin(HttpxPluginBase):
    """Python plugin for crawli.net using httpx.

    crawli.net is a German download search engine that aggregates
    results from various sources. It provides direct links to source
    sites without hosting content itself.
    """

    name = "crawli"
    provides = "download"
    default_language = "de"
    _domains = _DOMAINS

    categories: dict[int, str] = {
        2000: "Movies",
        5000: "TV",
        4000: "Games",
        3000: "Music",
        5020: "Apps",
    }

    async def _search_page(
        self,
        query: str,
        category_path: str,
        page_num: int = 1,
    ) -> tuple[list[dict[str, str]], int]:
        """Fetch one search results page.

        Returns ``(results, max_page_number)``.
        """
        encoded_query = quote_plus(query)
        if page_num > 1:
            url = f"{self.base_url}/{category_path}/{encoded_query}/p-{page_num}/"
        else:
            url = f"{self.base_url}/{category_path}/{encoded_query}/"

        resp = await self._safe_fetch(url, context="search_page")
        if resp is None:
            return [], 1

        parser = _SearchResultParser()
        parser.feed(resp.text)

        self._log.info(
            "crawli_search_page",
            query=query,
            category=category_path,
            page=page_num,
            count=len(parser.results),
            max_page=parser.max_page,
        )
        return parser.results, parser.max_page

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search crawli.net and return results.

        Paginates through search pages to collect up to 1000 results.
        """
        await self._ensure_client()
        await self._verify_domain()

        # Determine category path segment
        category_path = "all"
        if category is not None:
            category_path = _CATEGORY_PATH_MAP.get(category, "all")

        # Fetch first page to learn pagination extent
        first_results, max_page = await self._search_page(query, category_path)
        all_results = list(first_results)

        if not all_results:
            return []

        # Fetch remaining pages sequentially (crawli returns 10/page)
        pages_to_fetch = min(max_page, _MAX_PAGES)
        page_num = 2
        while len(all_results) < self._max_results and page_num <= pages_to_fetch:
            page_results, _ = await self._search_page(
                query, category_path, page_num
            )
            if not page_results:
                break
            all_results.extend(page_results)
            page_num += 1

        all_results = all_results[: self._max_results]

        # Convert to SearchResult
        torznab_cat = category if category else 2000
        results: list[SearchResult] = []
        for item in all_results:
            source_url = item["source_url"]
            results.append(
                SearchResult(
                    title=item["title"],
                    download_link=source_url,
                    source_url=source_url,
                    category=torznab_cat,
                    published_date=item.get("date", ""),
                    description=item.get("description", ""),
                )
            )

        return results


plugin = CrawliPlugin()
