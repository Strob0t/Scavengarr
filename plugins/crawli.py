"""crawli.net Python plugin for Scavengarr.

Scrapes crawli.net (German download search engine) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Search via GET /{section}/{query}/ with spaces as +
- Category filtering via the section path (film, serie, spiel, music, apps);
  results are labelled by the section that listed them
- The page arrives base64-encoded for a script to write (since 2026-09)
- Pagination up to 1000 items (10 results/page, max 100 pages)
- Single-stage: title, source URL, date, description all on search page

Multi-domain support: crawli.net, www.crawli.net.
No authentication required.
"""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser
from urllib.parse import quote_plus

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
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

# crawli section (URL path segment) -> Torznab category of its results
_SECTION_CATEGORIES: dict[str, int] = {
    "film": 2000,
    "serie": 5000,
    "spiel": 4050,
    "music": 3000,
    "apps": 4000,
}
# The sections to search for a category (PC: applications and games). TV
# searches everything: /serie/ lists series pages, while the episode releases
# are filed as films or unsorted (checked live)
_CATEGORY_SECTIONS: dict[int, tuple[str, ...]] = {
    2000: ("film",),
    5000: ("all",),
    4050: ("spiel",),
    4000: ("apps", "spiel"),
    3000: ("music",),
}
# The title link's class marks the row's section (live): sres1 apps,
# sres2 music, sres3 film, sres5 spiel, sres7 serie; sres0/sres6 unsorted
_KIND_RE = re.compile(r"sres\d+")
_KIND_CATEGORIES: dict[str, int] = {
    "sres1": 4000,
    "sres2": 3000,
    "sres3": 2000,
    "sres5": 4050,
    "sres7": 5000,
}
# Episodes and season packs are TV, even when filed as films or unsorted
_SERIES_RE = re.compile(r"\bS\d{1,2}(?:E\d{1,3})?\b|\bStaffel\b", re.IGNORECASE)


def _row_category(row: dict[str, str]) -> int:
    """Torznab category of a result row, from its section class and title."""
    category = _KIND_CATEGORIES.get(row.get("kind", ""), 8000)
    if category in (2000, 8000) and _SERIES_RE.search(row["title"]):
        return 5000
    return category


# The page is sent as ``var str = "<base64>"`` for a script to write
_PAYLOAD_RE = re.compile(r'var str = "([A-Za-z0-9+/=]+)"')


def _decode_page(html: str) -> str:
    """The page HTML, decoded when it came base64-encoded."""
    m = _PAYLOAD_RE.search(html)
    if m is None:
        return html
    try:
        return base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
    except ValueError:
        return html


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
        self._current_kind = ""
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
                    "kind": self._current_kind,
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

        # Title link: <a class="sres3">, the class marks the section
        kind = next((c for c in classes if _KIND_RE.fullmatch(c)), "")
        if tag == "a" and self._in_sres and kind:
            self._in_title_a = True
            self._current_kind = kind
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
        parser.feed(_decode_page(resp.text))

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
        sections: tuple[str, ...] = ("all",)
        if category is not None:
            category = served_category(category, _SECTION_CATEGORIES.values())
            if category is None:
                return []  # no section of the site has this category
            sections = _CATEGORY_SECTIONS[category]
        await self._ensure_client()
        await self._verify_domain()

        results: list[SearchResult] = []
        for section in sections:
            for item in await self._search_section(query, section):
                row_category = _row_category(item)
                if not category_matches(category, row_category):
                    continue  # e.g. an episode filed as a film
                source_url = item["source_url"]
                results.append(
                    SearchResult(
                        title=item["title"],
                        download_link=source_url,
                        source_url=source_url,
                        category=row_category,
                        published_date=item.get("date", ""),
                        description=item.get("description", ""),
                    )
                )
        return results[: self.effective_max_results]

    async def _search_section(self, query: str, section: str) -> list[dict[str, str]]:
        """Search rows of one section over its result pages (10 per page)."""
        first_results, max_page = await self._search_page(query, section)
        all_results = list(first_results)
        if not all_results:
            return []

        pages_to_fetch = min(max_page, _MAX_PAGES)
        page_num = 2
        while (
            len(all_results) < self.effective_max_results and page_num <= pages_to_fetch
        ):
            page_results, _ = await self._search_page(query, section, page_num)
            if not page_results:
                break
            all_results.extend(page_results)
            page_num += 1
        return all_results[: self.effective_max_results]


plugin = CrawliPlugin()
