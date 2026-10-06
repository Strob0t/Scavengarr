"""movieblog.to Python plugin for Scavengarr.

Scrapes movieblog.to (German DDL blog, WordPress-based) with:
- httpx for all requests (server-rendered HTML)
- WordPress search via /?s={query}
- Pagination via /page/{N}/?s={query} (~10 results/page, up to 100 pages)
- Two-stage: search results page gives titles + detail URLs,
  detail pages contain filecrypt.cc download containers
- Category detection from category tag links in search results
  ("Serie" → TV 5000, everything else → Movie 2000)
- Download links: filecrypt.cc containers (rapidgator, ddownload, nitroflare)

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["movieblog.to"]
_MAX_PAGES = 100  # ~10 results/page → 100 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_FILECRYPT_RE = re.compile(r"https?://(?:www\.)?filecrypt\.cc/Container/\w+\.html")
_SIZE_RE = re.compile(
    r"Größe:\s*([\d.,]+\s*(?:[KMGT]i?)?B)",
    re.IGNORECASE,
)

# WordPress category slugs that indicate TV content.
_TV_SLUGS = frozenset({"serie"})

# Hoster label detection from link text.
_HOSTER_MAP: dict[str, str] = {
    "rapidgator": "rapidgator",
    "ddownload": "ddownload",
    "nitroflare": "nitroflare",
    "1fichier": "1fichier",
    "turbobit": "turbobit",
}


# ---------------------------------------------------------------------------
# Search result parser (search listing page)
# ---------------------------------------------------------------------------
class _SearchResultParser:
    """Parse movieblog.to search results (selectolax).

    Structure per result::

        <div class="post">
          <div class="post-date">
            <span class="post-month">Feb.</span>
            <span class="post-day">07</span>
          </div>
          <h1 id="post-132474">
            <a href="URL" title="...">TITLE</a>
          </h1>
          ...
          <p class="info_x">Thema:
            <a href="/category/drama/" rel="category tag">Drama</a>,
            <a href="/category/serie/" rel="category tag">Serie</a>
            ...
          </p>
        </div>

    A result is a div whose class is exactly ``post``: the last link with
    an href in one of its ``h1`` names it, the ``category tag`` links of
    its ``info_x`` paragraphs give its category.
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str | int]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for post in tree.css("div.post"):
            if (post.attributes.get("class") or "").strip() != "post":
                continue
            links = [a for a in post.css("h1 a") if a.attributes.get("href")]
            title = links[-1].text().strip() if links else ""
            if title:
                self.results.append(
                    {
                        "title": title,
                        "url": links[-1].attributes.get("href") or "",
                        "category": _detect_category(_category_hrefs(post)),
                    }
                )


def _category_hrefs(post: LexborNode) -> list[str]:
    """The targets of the ``category tag`` links in a post's ``info_x``."""
    return [
        link.attributes.get("href") or ""
        for info in post.css("p[class*='info_x']")
        for link in info.css("a[rel]")
        # [rel*=...] would match case-insensitively
        if "category" in (rel := link.attributes.get("rel") or "") and "tag" in rel
    ]


def _detect_category(cat_hrefs: list[str]) -> int:
    """Map WordPress category hrefs to Torznab category IDs."""
    for href in cat_hrefs:
        slug = href.rstrip("/").rsplit("/", 1)[-1].lower()
        if slug in _TV_SLUGS:
            return 5000
    return 2000


# ---------------------------------------------------------------------------
# Pagination parser
# ---------------------------------------------------------------------------
class _PaginationParser:
    """Extract next page URL from navigation_x div (selectolax).

    Structure::

        <div class="navigation_x">
          <div class="alignleft">
            <a href="/page/2/?s=query">« vorherige Beiträge</a>
          </div>
          <div class="alignright">
            <a href="/page/2/?s=query">Nächste Seite »</a>
          </div>
        </div>

    The next page is the first ``alignright`` link whose first text reads
    "Nächste Seite".
    """

    def __init__(self) -> None:
        self.next_page_url: str = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        nav = "div[class*='navigation_x'] div[class*='alignright'] a[href]"
        for link in tree.css(nav):
            href = link.attributes.get("href") or ""
            if href and "Nächste Seite" in _first_text(link):
                self.next_page_url = href
                return


def _first_text(node: LexborNode) -> str:
    """The first text node under *node*, "" without one."""
    for child in node.traverse(include_text=True):
        if child.is_text_node:
            return child.text()
    return ""


# ---------------------------------------------------------------------------
# Detail page parser
# ---------------------------------------------------------------------------
class _DetailPageParser:
    """Parse movieblog.to detail page for download links and metadata (selectolax).

    Download structure::

        <strong>Download: </strong>
        <a href="https://filecrypt.cc/Container/XXX.html">Rapidgator.net</a>
        ...
        <strong>Mirror #1: </strong>
        <a href="https://filecrypt.cc/Container/YYY.html">Ddownload.com</a>

    Size structure::

        <strong>Größe: </strong>7,72 GB
    """

    def __init__(self) -> None:
        self.download_links: list[dict[str, str]] = []
        self._all_text = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for link in tree.css("a[href*='filecrypt.cc/Container/']"):
            self._add_link(link.attributes.get("href") or "", link.text())
        # All text, a space before each text node (tags separate words)
        self._all_text += " " + tree.text(separator=" ")

    def _add_link(self, href: str, text: str) -> None:
        """Store a filecrypt link with hoster label."""
        if not _FILECRYPT_RE.match(href):
            return
        hoster = _detect_hoster(text.strip().lower())
        if not any(d["link"] == href for d in self.download_links):
            self.download_links.append({"hoster": hoster, "link": href})

    def extract_size(self) -> str:
        """Extract file size from the page text."""
        m = _SIZE_RE.search(self._all_text)
        if m:
            return m.group(1).strip()
        return ""


def _detect_hoster(text: str) -> str:
    """Detect hoster name from link text."""
    for keyword, label in _HOSTER_MAP.items():
        if keyword in text:
            return label
    return "filecrypt"


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class MovieblogPlugin(HttpxPluginBase):
    """movieblog.to httpx plugin."""

    name = "movieblog"
    provides = "download"
    _domains = _DOMAINS
    _max_concurrent = 3

    # ------------------------------------------------------------------
    # Search page fetching
    # ------------------------------------------------------------------

    async def _search_page(
        self,
        query: str,
        page: int,
    ) -> tuple[list[dict[str, str | int]], str]:
        """Fetch one page, return (results, next_page_url)."""
        if page == 1:
            url = f"{self.base_url}/?s={quote_plus(query)}"
        else:
            url = f"{self.base_url}/page/{page}/?s={quote_plus(query)}"

        resp = await self._safe_fetch(url)
        if resp is None or resp.status_code != 200:
            return [], ""

        html = resp.text
        parser = await parse_page(_SearchResultParser(), html)

        pag_parser = await parse_page(_PaginationParser(), html)

        self._log.info(
            "movieblog_search_page",
            query=query,
            page=page,
            results=len(parser.results),
            has_next=bool(pag_parser.next_page_url),
        )
        return parser.results, pag_parser.next_page_url

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str | int]]:
        """Paginate through search results up to effective_max_results."""
        all_results: list[dict[str, str | int]] = []

        first_page, next_url = await self._search_page(query, 1)
        if not first_page:
            return []
        all_results.extend(first_page)

        page = 2
        while (
            next_url
            and page <= _MAX_PAGES
            and len(all_results) < self.effective_max_results
        ):
            page_results, next_url = await self._search_page(query, page)
            if not page_results:
                break
            all_results.extend(page_results)
            page += 1

        return all_results[: self.effective_max_results]

    # ------------------------------------------------------------------
    # Detail page scraping
    # ------------------------------------------------------------------

    async def _scrape_detail(
        self,
        item: dict[str, str | int],
    ) -> dict[str, str | int] | None:
        """Scrape a single detail page for download links."""
        url = str(item.get("url", ""))
        if not url:
            return None

        resp = await self._safe_fetch(url)
        if resp is None or resp.status_code != 200:
            self._log.warning("movieblog_detail_failed", url=url)
            return None

        parser = await parse_page(_DetailPageParser(), resp.text)

        if not parser.download_links:
            return None

        item = dict(item)
        item["download_links"] = parser.download_links  # type: ignore[assignment]
        size = parser.extract_size()
        if size:
            item["size"] = size
        return item

    async def _scrape_details_parallel(
        self,
        items: list[dict[str, str | int]],
    ) -> list[dict[str, str | int]]:
        """Scrape detail pages in parallel with bounded concurrency."""
        sem = self._new_semaphore()

        async def _bounded(item: dict[str, str | int]) -> dict[str, str | int] | None:
            async with sem:
                return await self._scrape_detail(item)

        tasks = [_bounded(item) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched: list[dict[str, str | int]] = []
        for r in results:
            if isinstance(r, dict):
                enriched.append(r)
        return enriched

    # ------------------------------------------------------------------
    # Result conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _item_to_result(item: dict) -> SearchResult | None:
        """Convert an enriched item dict to a SearchResult."""
        title = str(item.get("title", "")).strip()
        links = item.get("download_links", [])
        if not title or not links:
            return None

        return SearchResult(
            title=title,
            download_link=links[0]["link"],
            download_links=links,
            source_url=str(item.get("url", "")),
            category=int(item.get("category", 2000)),
            size=str(item.get("size", "")) or None,
            release_name=title,
        )

    # ------------------------------------------------------------------
    # Category filtering
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Main search entry point
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search movieblog.to and return results with download links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, (2000, 5000))
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        all_items = [
            item
            for item in all_items
            if category_matches(category, int(item.get("category", 2000)))
        ]
        if not all_items:
            return []

        enriched = await self._scrape_details_parallel(all_items)

        results: list[SearchResult] = []
        for item in enriched:
            sr = self._item_to_result(item)
            if sr is not None:
                results.append(sr)

        return results


plugin = MovieblogPlugin()
