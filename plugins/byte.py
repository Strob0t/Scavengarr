"""byte.to Python plugin for Scavengarr.

Scrapes byte.to (German DDL site) with:
- httpx for all requests (server-rendered HTML; Cloudflare challenges go
  through the shared browser fallback of HttpxPluginBase)
- Search via /?q=query&t=1 over every group; a category request keeps the
  rows of its category (the site's c= lists only entries filed directly
  under a group, not its subgroups)
- Multi-page pagination (200 items per page, up to 5 pages)
- Download links from the per-hoster link widgets (``/widgets/button.php``)
  embedded on detail pages
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["byte.to"]
_MAX_PAGES = 5
_WIDGET_PATH = "/widgets/button.php"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Site category name (lowercase) → Torznab category ID; the live menu,
# checked 2026-09-29
_SITE_CATEGORY_MAP: dict[str, int] = {
    # Filme
    **dict.fromkeys(
        (
            "filme",
            "kinofilme",
            "dvd",
            "microhd",
            "microhd - 4k",
            "microhd - 3d",
            "hd - 3d",
            "sd - x264",
            "sd - xvid",
            "hd - 720p",
            "hd - 1080p",
            "hd - 1080p x265",
            "uhd - 2160p",
        ),
        2000,
    ),
    **dict.fromkeys(("hd - 1080p englisch", "hd - 720p englisch"), 2010),
    # Television
    **dict.fromkeys(
        ("television", "tv", "serien", "microhd serien", "ganze staffeln"), 5000
    ),
    "einzelne folgen": 5000,
    **dict.fromkeys(("dokumentation", "microhd dokus"), 5080),
    # Spiele
    **dict.fromkeys(("spiele", "pc", "win", "mac os", "virtual reality"), 4050),
    **dict.fromkeys(("konsolen", "ps5"), 1000),
    "nintendo wii": 1030,
    "ps4": 1180,
    # Programme
    **dict.fromkeys(("programme", "freeware", "windows", "linux"), 4000),
    "mac": 4030,
    "android": 4070,
    # Musik
    **dict.fromkeys(
        (
            "musik",
            "alben",
            "singles",
            "sampler",
            "charts",
            "soundtracks",
            "volksmusik",
            "schlager",
            "diskografie",
            "austria",
            "country",
        ),
        3000,
    ),
    "lossless": 3040,
    "konzerte & videos": 3020,
    "hörbücher": 3030,
    # Bücher
    **dict.fromkeys(
        (
            "bücher",
            "ebooks",
            "comics",
            "magazine",
            "englische magazine",
            "magazine-zeitungen",
            "tageszeitungen",
        ),
        7000,
    ),
    # XxX
    **dict.fromkeys(
        (
            "xxx",
            "clips",
            "pics",
            "siterip",
            "ifeelmyself",
            "mydirtyhobby",
            "divx/xvid",
            "dvd/dvd9",
            "bluray/hdtv",
        ),
        6000,
    ),
}


class _SearchResultParser:
    """Extract search results from byte.to search page (selectolax).

    Parses ``<table class="SEARCH_ITEMLIST">`` for:
    - Title links inside ``<p class="TITLE"><a href="...">``
    - Category links with ``href="/?cat=N"``
    - Total hit count from ``<h1>Suche nach: ... (N Treffer)</h1>``
    - Pagination from ``<table class="NAVIGATION">``

    A title waits for the next category link; the next ``TITLE`` paragraph
    (or ``flush_pending()``) emits it without a category.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self.total_hits: int = 0
        self.max_page: int = 1
        self._base_url = base_url

        # Pending result (title found, waiting for category)
        self._pending_url = ""
        self._pending_title = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for h1 in tree.css("h1"):
            m = re.search(r"\((\d+)\s+Treffer\)", h1.text())
            if m:
                self.total_hits = int(m.group(1))
        # Title paragraphs and links in document order
        for node in tree.css("p, a"):
            if node.tag == "a":
                self._handle_a(node)
            elif _class_attr(node) == "TITLE":
                # Flush any pending result without category
                self.flush_pending()

    def _handle_a(self, link: LexborNode) -> None:
        href = link.attributes.get("href") or ""

        if href and _inside(link, "p", "TITLE"):
            title = link.text().strip()
            if title:
                self._pending_url = urljoin(self._base_url, href)
                self._pending_title = title
        elif "start=" in href and _inside(link, "table", "NAVIGATION"):
            m = re.search(r"start=(\d+)", href)
            if m:
                self.max_page = max(self.max_page, int(m.group(1)))
        elif href.startswith("/?cat=") and self._pending_url:
            self.results.append(
                {
                    "title": self._pending_title,
                    "url": self._pending_url,
                    "category": link.text().strip(),
                }
            )
            self._pending_url = ""
            self._pending_title = ""

    def flush_pending(self) -> None:
        """Emit any pending result that has no category yet."""
        if self._pending_url:
            self.results.append(
                {
                    "title": self._pending_title,
                    "url": self._pending_url,
                    "category": "",
                }
            )
            self._pending_url = ""
            self._pending_title = ""


class _DetailPageParser:
    """Extract metadata and link widget URLs from a byte.to detail page (selectolax).

    Finds:
    - Release name: first ``<td>`` text matching scene-release pattern
    - Size / category: ``<td><B>Größe:</B> 3,98 GB</td>`` (label and value
      share a cell)
    - Link widgets: ``<iframe src=".../widgets/button.php?...">``, one per
      hoster link

    Only cells without cells inside count: the layout cells around the
    detail tables hold the whole page.
    """

    def __init__(self) -> None:
        self.release_name: str = ""
        self.size: str = ""
        self.category: str = ""
        self.widget_urls: list[str] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for iframe in tree.css("iframe"):
            src = (iframe.attributes.get("src") or "").strip()
            if _WIDGET_PATH in src and src not in self.widget_urls:
                self.widget_urls.append(src)
        for td in tree.css("td"):
            # css() matches the cell itself too
            if len(td.css("td")) == 1:
                self._read_cell(td.text())

    def _read_cell(self, cell_text: str) -> None:
        text = " ".join(cell_text.split())
        label, sep, value = text.partition(":")
        label = label.lower()

        if sep and value.strip():
            if label == "größe" and not self.size:
                self.size = value.strip()
            elif label == "kategorie" and not self.category:
                self.category = value.strip()

        # Detect release name: scene pattern (dots, no spaces, 3+ dots)
        if (
            not self.release_name
            and " " not in text
            and text.count(".") >= 3
            and len(text) > 15
            and not text.startswith("http")
        ):
            self.release_name = text


class _WidgetLinkParser:
    """Extract the download link from a byte.to link widget (selectolax).

    Structure::

        <a href="https://hide.cx/container/..." class="loadbutton">
          <span class="green-dot" title="Online"></span>
          <img src="/widgets/favicons/rapidgator.net.ico" title="rapidgator.net">
          rapidgator.net
        </a>

    Links flagged offline (``red-dot``) are skipped.
    """

    def __init__(self) -> None:
        self.links: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for link in tree.css("a"):
            href = (link.attributes.get("href") or "").strip()
            if href.startswith("http"):
                self._add_link(link, href)

    def _add_link(self, link: LexborNode, href: str) -> None:
        # The last host icon and the last status dot of the link count
        img_host = ""
        offline = False
        for node in link.css("img, span"):
            if node.tag == "img":
                attrs = node.attributes
                host = attrs.get("title") or attrs.get("alt") or ""
                if "." in host:
                    img_host = host.strip()
            else:
                offline = "red-dot" in classes(node)

        host = img_host or link.text().strip().lower().removeprefix("online")
        hoster = host.strip().split(".")[0].lower()
        if hoster and not offline:
            self.links.append({"hoster": hoster, "link": href})


def _class_attr(node: LexborNode) -> str:
    """The class attribute of *node* in upper case (the site writes TITLE)."""
    return (node.attributes.get("class") or "").upper()


def _inside(node: LexborNode, tag: str, class_attr: str) -> bool:
    """Whether *node* sits in a *tag* element whose upper-case class
    attribute is *class_attr*."""
    return any(
        parent.tag == tag and _class_attr(parent) == class_attr
        for parent in ancestors(node)
    )


def _site_category_to_torznab(category_name: str) -> int:
    """Map site category name to Torznab category ID (8000 if unknown)."""
    return _SITE_CATEGORY_MAP.get(category_name.lower().strip(), 8000)


class BytePlugin(HttpxPluginBase):
    """Python plugin for byte.to using httpx."""

    name = "byte"
    version = "1.1.0"
    provides = "download"

    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        page_num: int = 1,
    ) -> tuple[list[dict[str, str]], int, int]:
        """Fetch a single search results page.

        Returns ``(results, total_hits, max_page)``.
        """
        params = {"q": query, "t": "1"}
        if page_num > 1:
            params.update({"h": "1", "e": "0", "start": str(page_num)})

        html = await self._fetch_text(
            f"{self.base_url}/", params=params, context="search_page"
        )
        if html is None:
            return [], 0, 1

        parser = await parse_page(_SearchResultParser(self.base_url), html)
        parser.flush_pending()

        self._log.info(
            "byte_search_page",
            query=query,
            page=page_num,
            results=len(parser.results),
            total_hits=parser.total_hits,
            max_page=parser.max_page,
        )
        return parser.results, parser.total_hits, parser.max_page

    async def _fetch_widget_links(self, widget_url: str) -> list[dict[str, str]]:
        """Fetch one link widget and return its download link(s)."""
        html = await self._fetch_text(widget_url, context="link_widget")
        if html is None:
            return []
        parser = await parse_page(_WidgetLinkParser(), html)
        return parser.links

    async def _scrape_detail(self, result: dict[str, str]) -> SearchResult | None:
        """Scrape a detail page for metadata and download links."""
        html = await self._fetch_text(result["url"], context="detail_page")
        if html is None:
            return None

        detail_parser = await parse_page(_DetailPageParser(), html)

        widget_urls = [urljoin(self.base_url, u) for u in detail_parser.widget_urls]
        widget_links = await asyncio.gather(
            *[self._fetch_widget_links(u) for u in widget_urls]
        )
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for link in (link for group in widget_links for link in group):
            if link["link"] not in seen:
                seen.add(link["link"])
                links.append(link)
        # Some widgets link to byte's own ``go.php?hash=`` redirector
        links = await self._resolve_own_links(links)

        if not links:
            self._log.debug("byte_no_links", url=result["url"])
            return None

        title = detail_parser.release_name or result.get("title", "Unknown")
        category_name = detail_parser.category or result.get("category", "")

        return SearchResult(
            title=title,
            download_link=links[0]["link"],
            download_links=links,
            source_url=result["url"],
            size=detail_parser.size or None,
            category=_site_category_to_torznab(category_name),
        )

    async def _search_rows(
        self, query: str, category: int | None
    ) -> list[dict[str, str]]:
        """Result rows of *category* over the search pages, before their pages
        are loaded.

        Every group is searched: the site's ``c=`` lists only entries filed
        directly under a group, not its subgroups (``c=1``, Filme, and
        ``c=2``, Television, answered every search with the empty search
        form; checked 2026-10-05)."""

        def _wanted(rows: list[dict[str, str]]) -> list[dict[str, str]]:
            return [
                r
                for r in rows
                if category_matches(
                    category, _site_category_to_torznab(r.get("category", ""))
                )
            ]

        first_results, _, max_page = await self._search_page(query)
        rows = _wanted(first_results)
        limit = self.effective_max_results

        pages_needed = min(max_page, _MAX_PAGES)
        for page_num in range(2, pages_needed + 1):
            if len(rows) >= limit:
                break
            more_results, _, _ = await self._search_page(query, page_num)
            rows.extend(_wanted(more_results))
            if not more_results:
                break
        return rows[:limit]

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search byte.to and return results with download links."""
        if category is not None:
            category = served_category(category, _SITE_CATEGORY_MAP.values())
            if category is None:
                return []  # the site has no category for it
        await self._ensure_client()
        await self._verify_domain()

        all_results = await self._search_rows(query, category)
        if not all_results:
            return []

        # Scrape detail pages in parallel with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded_scrape(
            r: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r)

        results = await asyncio.gather(
            *[_bounded_scrape(r) for r in all_results],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                self._log.warning("byte_detail_error", error=str(r))
        return [r for r in results if isinstance(r, SearchResult)]


plugin = BytePlugin()
