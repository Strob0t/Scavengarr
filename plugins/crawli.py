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
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    is_series_title,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import classes
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


def _row_category(row: dict[str, str]) -> int:
    """Torznab category of a result row, from its section class and title."""
    category = _KIND_CATEGORIES.get(row.get("kind", ""), 8000)
    # Episodes and season packs are TV, even when filed as films or unsorted
    if category in (2000, 8000) and is_series_title(row["title"]):
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
def _last_text(node: LexborNode, selector: str) -> str:
    """The stripped text of the last match of *selector* in *node*."""
    matches = node.css(selector)
    return matches[-1].text().strip() if matches else ""


class _SearchResultParser:
    """Parse crawli.net search results page (selectolax).

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

    A result needs its ``scont`` div, a title link and a source URL; the
    last title link, source URL and date of the box count, and the
    description joins the paragraphs of the ``scont`` div.

    Pagination is in ``#foot > span.pages > a`` with ``p-N`` links.
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []
        self.max_page: int = 1

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for box in tree.css("div.entry-content.sresd"):
            self._add_result(box)
        # Pagination links: <a href="//crawli.net/{cat}/{query}/p-N/">
        for link in tree.css("div#foot a"):
            m = re.search(r"/p-(\d+)/", link.attributes.get("href") or "")
            if m:
                self.max_page = max(self.max_page, int(m.group(1)))

    def _add_result(self, box: LexborNode) -> None:
        content = box.css_first("div.scont")
        if content is None:
            return  # a result ends with its content div
        kind = title = ""
        for link in box.css("strong.sres a"):
            # Title link: <a class="sres3">, the class marks the section
            link_kind = next((c for c in classes(link) if _KIND_RE.fullmatch(c)), "")
            if link_kind:
                kind, title = link_kind, link.text().strip()
        source_url = _last_text(box, "address.resl")
        if not title or not source_url:
            return
        # Ensure source URL has a scheme
        if not source_url.startswith("http"):
            source_url = f"https://{source_url}"
        self.results.append(
            {
                "kind": kind,
                "title": title,
                "source_url": source_url,
                "date": _last_text(box, "small.rtime"),
                "description": "".join(p.text() for p in content.css("p")).strip(),
            }
        )


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
