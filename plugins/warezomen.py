"""warezomen.com Python plugin for Scavengarr.

Scrapes warezomen.com — a direct download link aggregator that serves a flat
HTML table of external download links per search query.

- No authentication required, no Cloudflare
- Static HTML (uses httpx, not Playwright)
- Pagination: 60 results per page, up to _MAX_PAGES pages
- Category filtering by site type (Movie, TV, Music, Software, Games)
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_BASE_URL = "https://warezomen.com"
_MAX_PAGES = 3

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Site type string → Torznab category ID
_CATEGORY_MAP: dict[str, int] = {
    "movie": 2000,
    "tv": 5000,
    "music": 3000,
    "software": 4000,
    "games": 1000,
    "other": 7020,
}

# Reverse: Torznab category → site type strings that match
_TORZNAB_TO_TYPES: dict[int, set[str]] = {}
for _type, _cat in _CATEGORY_MAP.items():
    _TORZNAB_TO_TYPES.setdefault(_cat, set()).add(_type)


def _slugify(query: str) -> str:
    """Convert a search query to a URL slug.

    Lowercase, strip non-alphanumeric (except hyphens/spaces),
    collapse whitespace into single hyphens.
    """
    slug = query.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug.strip("-")


class _ResultTableParser(HTMLParser):
    """Parse the download table from warezomen.com search results.

    Extracts rows from ``table.download > tbody > tr``, skipping separator
    rows (``td.d``).  Each result row has 4 cells:
      1. ``td.n`` with ``a[title][href]`` → title + download link
      2. ``td.n`` → hoster/site name
      3. ``td.t2`` → type (Movie, TV, etc.)
      4. ``td`` → date
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []

        self._in_table = False
        self._in_row = False
        self._is_separator = False
        self._cell_index = 0
        self._in_cell = False
        self._cell_text = ""

        # Current row data
        self._row_title = ""
        self._row_link = ""
        self._row_site = ""
        self._row_type = ""
        self._row_date = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "table":
            classes = (attr_dict.get("class") or "").split()
            if "download" in classes:
                self._in_table = True
            return

        if not self._in_table:
            return

        if tag == "tr":
            self._in_row = True
            self._is_separator = False
            self._cell_index = 0
            self._row_title = ""
            self._row_link = ""
            self._row_site = ""
            self._row_type = ""
            self._row_date = ""
            return

        if tag == "td" and self._in_row:
            classes = (attr_dict.get("class") or "").split()
            if "d" in classes:
                self._is_separator = True
                return
            self._in_cell = True
            self._cell_text = ""
            return

        if tag == "a" and self._in_cell and self._cell_index == 0:
            # First cell: extract title from title attr and href
            title = attr_dict.get("title", "")
            href = attr_dict.get("href", "")
            if title and href:
                self._row_title = title
                self._row_link = href

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_table:
            self._in_table = False
            return

        if tag == "td" and self._in_cell:
            self._in_cell = False
            text = self._cell_text.strip()

            if self._cell_index == 1:
                self._row_site = text
            elif self._cell_index == 2:
                self._row_type = text
            elif self._cell_index == 3:
                self._row_date = text

            self._cell_index += 1
            return

        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._is_separator or not self._row_title or not self._row_link:
                return
            self.results.append({
                "title": self._row_title,
                "download_link": self._row_link,
                "site": self._row_site,
                "type": self._row_type,
                "date": self._row_date,
            })


class _PaginationParser(HTMLParser):
    """Extract 'Next Page' link from warezomen pagination."""

    def __init__(self) -> None:
        super().__init__()
        self.next_url: str | None = None
        self._in_pages_td = False
        self._last_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "td" and attr_dict.get("id") == "pages":
            self._in_pages_td = True
            return

        if tag == "a" and self._in_pages_td:
            self._last_href = attr_dict.get("href", "")

    def handle_data(self, data: str) -> None:
        if self._in_pages_td and "Next Page" in data and self._last_href:
            self.next_url = self._last_href

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_pages_td:
            self._in_pages_td = False


class WarezomenPlugin:
    """Python plugin for warezomen.com (static HTML, httpx-based)."""

    name = "warezomen"

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search warezomen.com and return results with download links."""
        slug = _slugify(query)
        if not slug:
            return []

        url = f"{_BASE_URL}/download/{slug}/"
        all_raw: list[dict[str, str]] = []

        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            for page_num in range(_MAX_PAGES):
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    log.warning(
                        "warezomen_fetch_failed",
                        url=url,
                        page=page_num + 1,
                        error=str(exc),
                    )
                    break

                html = resp.text

                # Parse results
                table_parser = _ResultTableParser()
                table_parser.feed(html)
                all_raw.extend(table_parser.results)

                # Check for next page
                pag_parser = _PaginationParser()
                pag_parser.feed(html)
                if pag_parser.next_url:
                    next_url = pag_parser.next_url
                    if not next_url.startswith("http"):
                        next_url = f"{_BASE_URL}{next_url}"
                    url = next_url
                else:
                    break

        # Filter by category if requested
        if category is not None:
            allowed_types = _TORZNAB_TO_TYPES.get(category)
            if allowed_types:
                all_raw = [
                    r for r in all_raw
                    if r["type"].lower() in allowed_types
                ]

        # Convert to SearchResult
        results: list[SearchResult] = []
        for raw in all_raw:
            type_lower = raw["type"].lower()
            cat = _CATEGORY_MAP.get(type_lower, 7020)

            results.append(SearchResult(
                title=raw["title"],
                download_link=raw["download_link"],
                download_links=[{
                    "hoster": raw["site"] or "unknown",
                    "link": raw["download_link"],
                }],
                source_url=f"{_BASE_URL}/download/{slug}/",
                category=cat,
                published_date=raw["date"] or None,
            ))

        log.info(
            "warezomen_search_complete",
            query=query,
            results_count=len(results),
        )
        return results


plugin = WarezomenPlugin()
