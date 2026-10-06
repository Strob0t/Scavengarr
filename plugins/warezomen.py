"""warezomen.com Python plugin for Scavengarr.

Scrapes warezomen.com (DDL aggregator) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Search via GET /download/{slugified_query}/
- Query slugification (spaces -> hyphens, special chars removed)
- Pagination up to 50 pages via "Next Page" link detection
- Single-stage: title, download_link, date from table rows

No authentication required.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

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
_DOMAINS = ["warezomen.com"]
_MAX_PAGES = 50

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Type column of the results table (live: t1 Software, t2 Movie, t3 Game,
# t4 TV, t5 Music, t6 Other) -> Torznab category
_TYPE_CATEGORIES: dict[str, int] = {
    "movie": 2000,
    "tv": 5000,
    "game": 4050,
    "software": 4000,
    "music": 3000,
    "other": 8000,
}


def _row_category(row: dict[str, str]) -> int:
    """Torznab category of a result row, from its Type column."""
    return _TYPE_CATEGORIES.get(row.get("type", "").strip().lower(), 8000)


def _slugify(text: str) -> str:
    """Convert query to URL-friendly slug (lowercase, hyphens)."""
    return _SLUG_RE.sub("-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------
class _SearchResultParser:
    """Parse warezomen.com search results table (selectolax).

    Each result row has structure::

        <tr>
          <td class="n"><a rel="nofollow" title="Full.Title"
              href="https://host.com/dl/id">Short title</a></td>
          <td class="n">hoster</td>
          <td class="t2">Type</td>
          <td>01-Nov-2025</td>
        </tr>

    Separator rows (``<td class="d" colspan="4">``) are ignored.

    Pagination is the "Next Page" link of ``<td id="pages">``, on the live
    site a row of the results table (no result)::

        <tr><td colspan="4" id="pages">[ 1 ] &nbsp;
          <a href="/download/windows/2/">Next Page &gt;</a></td></tr>
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for row in tree.css("table.download tbody tr"):
            self._add_row(row)
        # The pagination cell's "Next Page" link; a result's short title can
        # read "Next Page" too
        for link in tree.css("#pages a"):
            if link.text(strip=True).lower().startswith("next page"):
                self.next_page_url = link.attributes.get("href") or ""
                break

    def _add_row(self, row: LexborNode) -> None:
        cells = row.css("td")
        # A separator or the pagination cell drops its row
        if not cells or any(
            _is_separator(cell) or cell.attributes.get("id") == "pages"
            for cell in cells
        ):
            return
        # Title link: the last <a> of the first cell, with title attr and href
        links = cells[0].css("a")
        if not links:
            return
        title = links[-1].attributes.get("title") or ""
        href = links[-1].attributes.get("href") or ""
        if title and href:
            self.results.append(
                {
                    "title": title,
                    "download_link": href,
                    # 3rd td holds the type, the 4th the date
                    "type": _cell_text(cells, 2),
                    "published_date": _cell_text(cells, 3),
                }
            )


def _is_separator(cell: LexborNode) -> bool:
    """Whether *cell* is a separator cell (``<td class="d" colspan="4">``)."""
    return "d" in classes(cell) and bool(cell.attributes.get("colspan"))


def _cell_text(cells: list[LexborNode], index: int) -> str:
    """Text of the cell at *index*: its text pieces, each stripped, joined."""
    return cells[index].text(strip=True) if index < len(cells) else ""


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class WarezomenPlugin(HttpxPluginBase):
    """Python plugin for warezomen.com using httpx."""

    name = "warezomen"
    provides = "download"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        page_url: str | None = None,
    ) -> tuple[list[dict[str, str]], str]:
        """Fetch one search results page.

        Returns ``(results, next_page_url)``.  *next_page_url* is empty
        when there is no further page.
        """
        if page_url is None:
            slug = _slugify(query)
            page_url = f"{self.base_url}/download/{slug}/"

        resp = await self._safe_fetch(page_url, context="search_page")
        if resp is None:
            return [], ""

        parser = await parse_page(_SearchResultParser(), resp.text)

        next_url = ""
        if parser.next_page_url:
            next_url = urljoin(self.base_url, parser.next_page_url)

        self._log.info(
            "warezomen_search_page",
            url=page_url,
            count=len(parser.results),
            has_next=bool(next_url),
        )
        return parser.results, next_url

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search warezomen.com and return results.

        Paginates through search pages to collect up to 1000 results.
        """
        if category is not None:
            category = served_category(category, _TYPE_CATEGORIES.values())
            if category is None:
                return []  # the site has no rows of this category
        await self._ensure_client()
        await self._verify_domain()

        # Pages one by one; only rows of the requested category count
        all_items: list[dict[str, str]] = []
        next_url: str | None = None
        for _ in range(_MAX_PAGES):
            page_results, next_url = await self._search_page(query, next_url)
            all_items.extend(
                r for r in page_results if category_matches(category, _row_category(r))
            )
            if (
                not page_results
                or not next_url
                or len(all_items) >= self.effective_max_results
            ):
                break

        all_items = all_items[: self.effective_max_results]

        # Convert to SearchResult
        results: list[SearchResult] = []
        for item in all_items:
            results.append(
                SearchResult(
                    title=item["title"],
                    download_link=item["download_link"],
                    source_url=item["download_link"],
                    published_date=item.get("published_date", ""),
                    category=_row_category(item),
                )
            )

        return results


plugin = WarezomenPlugin()
