"""ddlspot.com Python plugin for Scavengarr.

Scrapes ddlspot.com (DDL indexer) with:
- Playwright for search and detail pages: Cloudflare Turnstile guards the
  search, and detail pages answer plain HTTP with an empty 200 body
- Bounded parallel detail fetching in the (cleared) browser context
- Flat table parsing (alternating title/detail row pairs)
- Download link extraction from detail page links-box

Categories: Software (4000), Games (4050), Movies (2000), TV (5000), E-Books (7000).
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus, urljoin

from patchright.async_api import Error as PlaywrightError
from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["www.ddlspot.com"]  # bare ddlspot.com redirects here
_MAX_PAGES = 50  # 20 results/page → 50 pages for 1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# DDLSpot type string → Torznab category ID
_CATEGORY_MAP: dict[str, int] = {
    "software": 4000,
    "games": 4050,
    "movies": 2000,
    "tv": 5000,
    "e-books": 7000,
}


def _row_category(row: dict[str, str]) -> int:
    """Torznab category of a result row, from its type column (8000 unknown)."""
    return _CATEGORY_MAP.get(row.get("type_str", "").strip().lower(), 8000)


class _SearchResultParser:
    """Parse the flat table from DDLSpot search results (selectolax).

    The table uses alternating row pairs:
    - Title row (``<tr class="row">``): title link, age, type, size, link count
    - Detail row (next ``<tr>``): filename and hoster info in ``<td class="links">``

    A title row counts once its detail row follows. The "Next Page" link
    sits below the table::

        <div class="box-content">[ 1 ] &nbsp;
          <a href="/o/oppenheimer/2/" title="Downloads | Page 2">Next Page »</a>
        </div>

    Produces a list of dicts with keys: title, detail_url, size, type_str.
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []
        self.next_page_url: str = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        # The last title row, until its detail row follows
        pending: dict[str, str] | None = None
        for tr in tree.css("table.download tbody tr"):
            if "row" in classes(tr):
                pending = _read_title_row(tr)
            elif pending is not None:
                if pending["title"] and pending["detail_url"]:
                    self.results.append(pending)
                pending = None
        # The last "Next Page" link outside the table
        for link in tree.css("a[href]"):
            href = link.attributes.get("href") or ""
            if (
                href
                and "next page" in link.text().lower()
                and not any(_is_download_table(node) for node in ancestors(link))
            ):
                self.next_page_url = href


def _read_title_row(row: LexborNode) -> dict[str, str]:
    """Title, detail URL, size and type of a title row ("" if missing)."""
    cells = row.css("td")
    links = cells[0].css("a") if cells else []
    title = detail_url = ""
    for link in links:
        detail_url = link.attributes.get("href") or detail_url
        # The link text is truncated ("...X265-Me.."); the title attribute
        # holds the full name as "<name> | <hosters>".
        title = (link.attributes.get("title") or "").split(" | ")[0].strip() or title
    if not title:
        # <b>Iron</b> <b>Man</b> ... are separate text nodes
        title = " ".join(text for link in links for text in _texts(link))
    return {
        "title": title,
        "detail_url": detail_url,
        "size": _cell_text(cells, 3),
        "type_str": _cell_text(cells, 2),
    }


def _cell_text(cells: list[LexborNode], index: int) -> str:
    """The last text of the cell at *index* ("" if none)."""
    texts = _texts(cells[index]) if index < len(cells) else []
    return texts[-1] if texts else ""


def _texts(node: LexborNode) -> list[str]:
    """The stripped, non-empty text nodes of *node*, in document order."""
    return [
        text
        for child in node.traverse(include_text=True)
        if (text := (child.text_content or "").strip())
    ]


def _is_download_table(node: LexborNode) -> bool:
    """Whether *node* is the search results table."""
    return node.tag == "table" and "download" in classes(node)


class _DetailPageParser:
    """Parse a DDLSpot detail page for download URLs (selectolax).

    Download URLs appear as plain text in ``<div class="links-box">``,
    one URL per line separated by ``<br>`` tags.
    """

    def __init__(self) -> None:
        self.urls: list[str] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for box in tree.css("div.links-box"):
            # A box inside another one is part of it
            if any(
                node.tag == "div" and "links-box" in classes(node)
                for node in ancestors(box)
            ):
                continue
            # One line per text node and newline: a <br> ends a URL too
            for line in box.text(separator="\n").split("\n"):
                url = line.strip()
                if url.startswith("http"):
                    self.urls.append(url)


class DDLSpotPlugin(PlaywrightPluginBase):
    """Python plugin for ddlspot.com using Playwright + httpx."""

    name = "ddlspot"
    version = "1.0.0"
    mode = "playwright"
    provides = "download"

    _domains = _DOMAINS

    async def _fetch_detail_page(self, url: str) -> str:
        """Fetch a detail page in the browser context ("" on failure).

        Plain HTTP gets an empty 200 body here; the browser context carries
        the Cloudflare clearance from the search page.
        """
        return await self._fetch_page_html(url, wait_for_idle=False)

    async def _fetch_detail_links(self, urls: list[str]) -> dict[str, list[str]]:
        """Fetch detail pages in parallel, return {detail_url: [download_urls]}."""
        sem = self._new_semaphore()

        async def _fetch_one(url: str) -> tuple[str, list[str]]:
            async with sem:
                html = await self._fetch_detail_page(url)
            parser = _DetailPageParser()
            parser.feed(html)
            return url, parser.urls

        pairs = await asyncio.gather(*(_fetch_one(url) for url in urls))
        return dict(pairs)

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
                    category=_row_category(row),
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
        if category is not None:
            category = served_category(category, _CATEGORY_MAP.values())
            if category is None:
                return []  # the site has no rows of this category
        await self._ensure_browser()

        encoded_query = quote_plus(query)
        search_url = f"{self.base_url}/search/?q={encoded_query}&m=1"

        # Paginate through search results (20 results/page)
        all_rows: list[dict[str, str]] = []
        current_url = search_url
        for _ in range(_MAX_PAGES):
            if len(all_rows) >= self.effective_max_results:
                break

            try:
                html = await self._fetch_search_page(current_url)
            except PlaywrightError as exc:
                # Keep the pages already collected
                self._log.warning(
                    "ddlspot_search_page_failed", url=current_url, error=str(exc)
                )
                break
            parser = _SearchResultParser()
            parser.feed(html)

            if not parser.results:
                break
            # Only rows of the requested category count (and get loaded)
            all_rows.extend(
                r
                for r in parser.results
                if category_matches(category, _row_category(r))
            )

            if not parser.next_page_url:
                break
            current_url = urljoin(self.base_url, parser.next_page_url)

        all_rows = all_rows[: self.effective_max_results]
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
