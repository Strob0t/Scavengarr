"""ddlspot.com Python plugin for Scavengarr.

Scrapes ddlspot.com (DDL indexer) with:
- Playwright for Cloudflare Turnstile bypass on search pages
- httpx for detail pages (no Cloudflare, parallel fetching)
- Flat table parsing (alternating title/detail row pairs)
- Download link extraction from detail page links-box

Categories: Software (4000), Games (4000), Movies (2000), TV (5000), E-Books (7000).
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin

import httpx

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["ddlspot.com"]
_MAX_PAGES = 50  # 20 results/page → 50 pages for 1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DDLSpot type string → Torznab category ID
_CATEGORY_MAP: dict[str, int] = {
    "software": 4000,
    "games": 4000,
    "movies": 2000,
    "tv": 5000,
    "e-books": 7000,
}

# Torznab category ID → DDLSpot URL segment (for filtering)
_REVERSE_CATEGORY_MAP: dict[int, str] = {
    4000: "software",
    2000: "movies",
    5000: "tv",
    7000: "e-books",
}


class _SearchResultParser(HTMLParser):
    """Parse the flat table from DDLSpot search results.

    The table uses alternating row pairs:
    - Title row (``<tr class="row">``): title link, age, type, size, link count
    - Detail row (next ``<tr>``): filename and hoster info in ``<td class="links">``

    Produces a list of dicts with keys: title, detail_url, size, type_str.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

        # State tracking
        self._in_table = False
        self._in_tbody = False
        self._in_title_row = False
        self._in_detail_row = False
        self._td_index = 0
        self._in_td = False
        self._in_a = False
        self._in_nav_a = False
        self._nav_a_href = ""
        self._nav_a_text = ""

        # Current row data
        self._current_title = ""
        self._current_detail_url = ""
        self._current_size = ""
        self._current_type = ""
        self._expect_detail_row = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)

        if tag == "table":
            classes = (attr_dict.get("class") or "").split()
            if "download" in classes:
                self._in_table = True

        # Track potential "Next Page" links outside the table
        if tag == "a" and not self._in_table:
            href = attr_dict.get("href", "")
            if href:
                self._in_nav_a = True
                self._nav_a_href = href
                self._nav_a_text = ""

        if not self._in_table:
            return

        if tag == "tbody":
            self._in_tbody = True

        if tag == "tr" and self._in_tbody:
            self._handle_tr(attr_dict)

        if tag == "td" and (self._in_title_row or self._in_detail_row):
            self._in_td = True
            self._td_index += 1

        if tag == "a" and self._in_title_row and self._td_index == 1:
            href = attr_dict.get("href", "")
            if href:
                self._current_detail_url = href
            self._in_a = True

    def _handle_tr(self, attr_dict: dict[str, str | None]) -> None:
        classes = (attr_dict.get("class") or "").split()
        if "row" in classes:
            # Title row
            self._in_title_row = True
            self._in_detail_row = False
            self._td_index = 0
            self._current_title = ""
            self._current_detail_url = ""
            self._current_size = ""
            self._current_type = ""
        elif self._expect_detail_row:
            # Detail row (follows title row)
            self._in_detail_row = True
            self._in_title_row = False
            self._td_index = 0

    def handle_data(self, data: str) -> None:
        if self._in_nav_a:
            self._nav_a_text += data

        if not self._in_td:
            return

        text = data.strip()
        if not text:
            return

        if self._in_title_row:
            if self._td_index == 1 and self._in_a:
                self._current_title += text
            elif self._td_index == 3:
                self._current_type = text
            elif self._td_index == 4:
                self._current_size = text

    def _handle_a_end(self) -> None:
        if self._in_a:
            self._in_a = False
        if self._in_nav_a:
            self._in_nav_a = False
            if "next page" in self._nav_a_text.strip().lower():
                self.next_page_url = self._nav_a_href

    def _handle_tr_end(self) -> None:
        if self._in_title_row:
            self._in_title_row = False
            self._expect_detail_row = True
        elif self._in_detail_row:
            self._in_detail_row = False
            self._expect_detail_row = False
            if self._current_title and self._current_detail_url:
                self.results.append(
                    {
                        "title": self._current_title,
                        "detail_url": self._current_detail_url,
                        "size": self._current_size,
                        "type_str": self._current_type,
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_table:
            self._in_table = False
            self._in_tbody = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "a":
            self._handle_a_end()
        elif tag == "td":
            self._in_td = False
        elif tag == "tr":
            self._handle_tr_end()


class _DetailPageParser(HTMLParser):
    """Parse a DDLSpot detail page for download URLs.

    Download URLs appear as plain text in ``<div class="links-box">``,
    one URL per line separated by ``<br>`` tags.
    """

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []
        self._in_links_box = False
        self._div_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "div":
            if self._in_links_box:
                self._div_depth += 1
            else:
                classes = (attr_dict.get("class") or "").split()
                if "links-box" in classes:
                    self._in_links_box = True
                    self._div_depth = 0

    def handle_data(self, data: str) -> None:
        if not self._in_links_box:
            return
        for line in data.split("\n"):
            line = line.strip()
            if line.startswith("http"):
                self.urls.append(line)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_links_box:
            if self._div_depth > 0:
                self._div_depth -= 1
            else:
                self._in_links_box = False


class DDLSpotPlugin(PlaywrightPluginBase):
    """Python plugin for ddlspot.com using Playwright + httpx."""

    name = "ddlspot"
    version = "1.0.0"
    mode = "playwright"
    provides = "download"
    default_language = "de"

    _domains = _DOMAINS

    async def _fetch_detail_links(self, urls: list[str]) -> dict[str, list[str]]:
        """Fetch detail pages in parallel, return {detail_url: [download_urls]}."""
        result: dict[str, list[str]] = {}
        sem = self._new_semaphore()

        async def _fetch_one(client: httpx.AsyncClient, url: str) -> None:
            async with sem:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    parser = _DetailPageParser()
                    parser.feed(resp.text)
                    result[url] = parser.urls
                except Exception as exc:  # noqa: BLE001
                    self._log.warning(
                        "ddlspot_detail_fetch_failed",
                        url=url,
                        error=str(exc),
                    )
                    result[url] = []

        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": self._user_agent},
        ) as client:
            tasks = [_fetch_one(client, url) for url in urls]
            await asyncio.gather(*tasks)

        return result

    async def _fetch_search_page(self, url: str) -> str:
        """Fetch a search page via Playwright and return HTML."""
        ctx = await self._ensure_context()
        page = await ctx.new_page()
        try:
            await self._navigate_and_wait(page, url)
            return await page.content()
        finally:
            if not page.is_closed():
                await page.close()

    def _build_results(
        self,
        rows: list[dict[str, str]],
        detail_links: dict[str, list[str]],
    ) -> list[SearchResult]:
        """Convert parsed rows + detail links into SearchResult objects."""
        results: list[SearchResult] = []
        for row in rows:
            detail_url = urljoin(self.base_url, row["detail_url"])
            download_urls = detail_links.get(detail_url, [])
            if not download_urls:
                continue

            type_str = row.get("type_str", "").lower()
            cat = _CATEGORY_MAP.get(type_str, 2000)

            dl_links = [
                {"hoster": _hoster_from_url(url), "link": url} for url in download_urls
            ]
            results.append(
                SearchResult(
                    title=row["title"],
                    download_link=download_urls[0],
                    download_links=dl_links,
                    source_url=detail_url,
                    size=row.get("size") or None,
                    category=cat,
                )
            )
        return results

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search ddlspot.com and return results with download links.

        Paginates through search pages to collect up to 1000 results
        by following "Next Page" links.
        """
        await self._ensure_browser()

        encoded_query = quote_plus(query)
        search_url = f"{self.base_url}/search/?q={encoded_query}&m=1"

        # Paginate through search results (20 results/page)
        all_rows: list[dict[str, str]] = []
        current_url = search_url
        for _ in range(_MAX_PAGES):
            if len(all_rows) >= self.effective_max_results:
                break

            html = await self._fetch_search_page(current_url)
            parser = _SearchResultParser()
            parser.feed(html)

            if not parser.results:
                break
            all_rows.extend(parser.results)

            if not parser.next_page_url:
                break
            current_url = urljoin(self.base_url, parser.next_page_url)

        all_rows = all_rows[: self.effective_max_results]
        if not all_rows:
            return []

        # Filter by category if specified
        if category is not None:
            cat_name = _REVERSE_CATEGORY_MAP.get(category, "")
            if cat_name:
                all_rows = [r for r in all_rows if r["type_str"].lower() == cat_name]

        if not all_rows:
            return []

        detail_urls = [urljoin(self.base_url, r["detail_url"]) for r in all_rows]
        detail_links = await self._fetch_detail_links(detail_urls)

        return self._build_results(all_rows, detail_links)


def _hoster_from_url(url: str) -> str:
    """Extract hoster name from URL domain."""
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
        if not host:
            return "unknown"
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts and parts[0] else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


plugin = DDLSpotPlugin()
