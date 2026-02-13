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
from html.parser import HTMLParser
from urllib.parse import urljoin

from scavengarr.domain.plugins.base import SearchResult
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


def _slugify(text: str) -> str:
    """Convert query to URL-friendly slug (lowercase, hyphens)."""
    return _SLUG_RE.sub("-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse warezomen.com search results table.

    Each result row has structure::

        <tr>
          <td class="n"><a rel="nofollow" title="Full.Title"
              href="https://host.com/dl/id">Short title</a></td>
          <td class="n">hoster</td>
          <td class="t2">Type</td>
          <td>01-Nov-2025</td>
        </tr>

    Separator rows (``<td class="d" colspan="4">``) are ignored.

    Pagination is detected via ``<td id="pages"><a>Next Page</a></td>``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

        # State tracking
        self._in_table = False
        self._in_tbody = False
        self._in_row = False
        self._td_index = 0
        self._in_td = False
        self._in_a = False
        self._is_separator = False
        self._in_pages_td = False
        self._in_pages_a = False
        self._pages_a_href = ""
        self._pages_a_text = ""

        # Current row data
        self._current_title = ""
        self._current_href = ""
        self._current_date = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        # Detect results table
        if tag == "table" and "download" in classes:
            self._in_table = True
            return

        if not self._in_table:
            # Detect pagination <td id="pages">
            if tag == "td" and attr_dict.get("id") == "pages":
                self._in_pages_td = True
            if tag == "a" and self._in_pages_td:
                self._in_pages_a = True
                self._pages_a_href = attr_dict.get("href", "") or ""
                self._pages_a_text = ""
            return

        if tag == "tbody":
            self._in_tbody = True

        if tag == "tr" and self._in_tbody:
            self._in_row = True
            self._td_index = 0
            self._is_separator = False
            self._current_title = ""
            self._current_href = ""
            self._current_date = ""

        if tag == "td" and self._in_row:
            self._td_index += 1
            self._in_td = True
            # Detect separator row: <td class="d" colspan="4">
            if "d" in classes and attr_dict.get("colspan"):
                self._is_separator = True

        # Title link: first <td class="n"> contains <a> with title attr and href
        if (
            tag == "a"
            and self._in_td
            and self._td_index == 1
            and not self._is_separator
        ):
            self._in_a = True
            self._current_title = attr_dict.get("title", "") or ""
            self._current_href = attr_dict.get("href", "") or ""

    def handle_data(self, data: str) -> None:
        if self._in_pages_a:
            self._pages_a_text += data

        if not self._in_td or self._is_separator:
            return

        # 4th td contains the date
        if self._td_index == 4:
            self._current_date += data.strip()

    def _handle_a_end(self) -> None:
        if self._in_a:
            self._in_a = False
        if self._in_pages_a:
            self._in_pages_a = False
            if "next page" in self._pages_a_text.strip().lower():
                self.next_page_url = self._pages_a_href

    def _handle_tr_end(self) -> None:
        if self._in_row:
            self._in_row = False
            if not self._is_separator and self._current_title and self._current_href:
                self.results.append(
                    {
                        "title": self._current_title,
                        "download_link": self._current_href,
                        "published_date": self._current_date,
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._handle_a_end()
        elif tag == "td":
            self._in_td = False
            if self._in_pages_td and not self._in_table:
                self._in_pages_td = False
        elif tag == "tr":
            self._handle_tr_end()
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "table" and self._in_table:
            self._in_table = False


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

        parser = _SearchResultParser()
        parser.feed(resp.text)

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
        await self._ensure_client()
        await self._verify_domain()

        first_results, next_url = await self._search_page(query)
        all_items = list(first_results)

        if not all_items:
            return []

        # Fetch remaining pages sequentially
        pages_fetched = 1
        while (
            next_url
            and len(all_items) < self.effective_max_results
            and pages_fetched < _MAX_PAGES
        ):
            page_results, next_url = await self._search_page(query, next_url)
            if not page_results:
                break
            all_items.extend(page_results)
            pages_fetched += 1

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
                    category=category if category else 2000,
                )
            )

        return results


plugin = WarezomenPlugin()
