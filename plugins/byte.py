"""byte.to Python plugin for Scavengarr.

Scrapes byte.to (German DDL site) with:
- httpx for all requests (server-rendered HTML; Cloudflare challenges go
  through the shared browser fallback of HttpxPluginBase)
- Advanced search via /?q=query&c=category_id&t=1
- Category filtering via dropdown category ID parameter
- Multi-page pagination (200 items per page, up to 5 pages)
- Download links from the per-hoster link widgets (``/widgets/button.php``)
  embedded on detail pages
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from scavengarr.domain.plugins.base import SearchResult
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

# Torznab category → site category ID for search URL parameter ``c=``.
_TORZNAB_TO_SITE_CATEGORY: dict[int, str] = {
    2000: "1",  # Filme
    5000: "2",  # Tv
    4000: "15",  # Spiele
    5020: "29",  # Programme
    3000: "99",  # Musik
    7000: "41",  # Bücher
    6000: "46",  # XxX
}

# Site category name (lowercase) → Torznab category ID.
_SITE_CATEGORY_MAP: dict[str, int] = {
    # Filme (2000)
    "kinofilme": 2000,
    "sd - xvid": 2000,
    "sd - x264": 2000,
    "dvd": 2000,
    "microhd": 2000,
    "hd - 720p": 2000,
    "hd - 1080p": 2000,
    "uhd - 2160p": 2000,
    "filme": 2000,
    # TV (5000)
    "serien": 5000,
    "dokumentation": 5000,
    "tv": 5000,
    # Spiele (4000)
    "pc": 4000,
    "win": 4000,
    "konsolen": 4000,
    "spiele": 4000,
    # Programme (5020)
    "programme": 5020,
    # Musik (3000)
    "alben": 3000,
    "charts": 3000,
    "musik": 3000,
    # Bücher (7000)
    "ebooks": 7000,
    "comics": 7000,
    "bücher": 7000,
    # Hörbücher (7020)
    "hörbücher": 7020,
    # XxX (6000)
    "xxx": 6000,
}


class _SearchResultParser(HTMLParser):
    """Extract search results from byte.to search page.

    Parses ``<table class="SEARCH_ITEMLIST">`` for:
    - Title links inside ``<p class="TITLE"><a href="...">``
    - Category links with ``href="/?cat=N"``
    - Total hit count from ``<h1>Suche nach: ... (N Treffer)</h1>``
    - Pagination from ``<table class="NAVIGATION">``
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.total_hits: int = 0
        self.max_page: int = 1
        self._base_url = base_url

        # Title tracking
        self._in_title_p = False
        self._in_title_a = False
        self._current_href = ""
        self._current_title = ""

        # Pending result (title found, waiting for category)
        self._pending_url = ""
        self._pending_title = ""

        # Category tracking
        self._in_cat_a = False
        self._current_category = ""

        # Hit count
        self._in_h1 = False
        self._h1_text = ""

        # Navigation
        self._in_nav = False

    def _handle_a_start(self, attr_dict: dict[str, str | None]) -> None:
        href = attr_dict.get("href", "") or ""

        if self._in_title_p and href:
            self._in_title_a = True
            self._current_href = href
            self._current_title = ""
        elif self._in_nav and href and "start=" in href:
            m = re.search(r"start=(\d+)", href)
            if m:
                page_num = int(m.group(1))
                if page_num > self.max_page:
                    self.max_page = page_num
        elif href.startswith("/?cat=") and self._pending_url:
            self._in_cat_a = True
            self._current_category = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        if tag == "table":
            cls = (attr_dict.get("class", "") or "").upper()
            if cls == "NAVIGATION":
                self._in_nav = True

        if tag == "p":
            cls = (attr_dict.get("class", "") or "").upper()
            if cls == "TITLE":
                # Flush any pending result without category
                if self._pending_url:
                    self.results.append(
                        {
                            "title": self._pending_title,
                            "url": self._pending_url,
                            "category": "",
                        }
                    )
                    self._pending_url = ""
                self._in_title_p = True

        if tag == "a":
            self._handle_a_start(attr_dict)

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_title_a:
            self._current_title += data
        if self._in_cat_a:
            self._current_category += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            m = re.search(r"\((\d+)\s+Treffer\)", self._h1_text)
            if m:
                self.total_hits = int(m.group(1))

        if tag == "table" and self._in_nav:
            self._in_nav = False

        if tag == "a":
            if self._in_title_a:
                self._in_title_a = False
                title = self._current_title.strip()
                href = self._current_href
                if title and href:
                    url = urljoin(self._base_url, href)
                    self._pending_url = url
                    self._pending_title = title

            if self._in_cat_a:
                self._in_cat_a = False
                category = self._current_category.strip()
                if self._pending_url:
                    self.results.append(
                        {
                            "title": self._pending_title,
                            "url": self._pending_url,
                            "category": category,
                        }
                    )
                    self._pending_url = ""
                    self._pending_title = ""

        if tag == "p" and self._in_title_p:
            self._in_title_p = False

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


class _DetailPageParser(HTMLParser):
    """Extract metadata and link widget URLs from a byte.to detail page.

    Finds:
    - Release name: first ``<td>`` text matching scene-release pattern
    - Size / category: ``<td><B>Größe:</B> 3,98 GB</td>`` (label and value
      share a cell)
    - Link widgets: ``<iframe src=".../widgets/button.php?...">``, one per
      hoster link
    """

    def __init__(self) -> None:
        super().__init__()
        self.release_name: str = ""
        self.size: str = ""
        self.category: str = ""
        self.widget_urls: list[str] = []

        self._in_td = False
        self._td_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self._in_td = True
            self._td_text = ""
        elif tag == "iframe":
            src = (dict(attrs).get("src", "") or "").strip()
            if _WIDGET_PATH in src and src not in self.widget_urls:
                self.widget_urls.append(src)

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._td_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag != "td" or not self._in_td:
            return

        self._in_td = False
        text = " ".join(self._td_text.split())
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


class _WidgetLinkParser(HTMLParser):
    """Extract the download link from a byte.to link widget.

    Structure::

        <a href="https://hide.cx/container/..." class="loadbutton">
          <span class="green-dot" title="Online"></span>
          <img src="/widgets/favicons/rapidgator.net.ico" title="rapidgator.net">
          rapidgator.net
        </a>

    Links flagged offline (``red-dot``) are skipped.
    """

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._in_a = False
        self._href = ""
        self._text = ""
        self._img_host = ""
        self._offline = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "a":
            href = (attr_dict.get("href", "") or "").strip()
            if href.startswith("http"):
                self._in_a = True
                self._href = href
                self._text = ""
                self._img_host = ""
                self._offline = False
        elif not self._in_a:
            return
        elif tag == "img":
            host = attr_dict.get("title") or attr_dict.get("alt") or ""
            if "." in host:
                self._img_host = host.strip()
        elif tag == "span":
            classes = (attr_dict.get("class", "") or "").split()
            self._offline = "red-dot" in classes

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._text += data

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_a:
            return

        self._in_a = False
        host = self._img_host or self._text.strip().lower().removeprefix("online")
        hoster = host.strip().split(".")[0].lower()
        if hoster and not self._offline:
            self.links.append({"hoster": hoster, "link": self._href})


def _site_category_to_torznab(category_name: str) -> int:
    """Map site category name to Torznab category ID."""
    return _SITE_CATEGORY_MAP.get(category_name.lower().strip(), 2000)


class BytePlugin(HttpxPluginBase):
    """Python plugin for byte.to using httpx."""

    name = "byte"
    version = "1.1.0"
    provides = "download"
    default_language = "de"

    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        site_category: str = "",
        page_num: int = 1,
    ) -> tuple[list[dict[str, str]], int, int]:
        """Fetch a single search results page.

        Returns ``(results, total_hits, max_page)``.
        """
        params = {"q": query, "t": "1"}
        if site_category:
            params["c"] = site_category
        if page_num > 1:
            params.update({"h": "1", "e": "0", "start": str(page_num)})

        html = await self._fetch_text(
            f"{self.base_url}/", params=params, context="search_page"
        )
        if html is None:
            return [], 0, 1

        parser = _SearchResultParser(self.base_url)
        parser.feed(html)
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
        parser = _WidgetLinkParser()
        parser.feed(html)
        return parser.links

    async def _scrape_detail(self, result: dict[str, str]) -> SearchResult | None:
        """Scrape a detail page for metadata and download links."""
        html = await self._fetch_text(result["url"], context="detail_page")
        if html is None:
            return None

        detail_parser = _DetailPageParser()
        detail_parser.feed(html)

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

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search byte.to and return results with download links."""
        await self._ensure_client()
        await self._verify_domain()

        site_category = _TORZNAB_TO_SITE_CATEGORY.get(category, "") if category else ""

        # Fetch first page
        first_results, _, max_page = await self._search_page(query, site_category)

        all_results = list(first_results)
        limit = self.effective_max_results

        # Fetch additional pages if needed
        pages_needed = min(max_page, _MAX_PAGES)
        for page_num in range(2, pages_needed + 1):
            if len(all_results) >= limit:
                break
            more_results, _, _ = await self._search_page(query, site_category, page_num)
            all_results.extend(more_results)
            if not more_results:
                break

        all_results = all_results[:limit]

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
