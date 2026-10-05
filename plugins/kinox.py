"""kinox.to Python plugin for Scavengarr.

Scrapes kinox.to / kinos.to / kinoz.to (German streaming aggregator) with:
- httpx for all requests (server-rendered pages)
- Search: GET /Search.html?q={query}
- Detail pages at /Stream/{slug}.html with streaming hoster info
- Movies, TV Series, and Documentaries

Multi-domain support with automatic fallback.
No authentication required.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "www22.kinox.to",
    "ww22.kinox.to",
    "www22.kinos.to",
    "ww22.kinos.to",
    "www22.kinoz.to",
    "ww22.kinoz.to",
    "www20.kinox.to",
    "www15.kinox.to",
    "www.kinox.to",
]


# Result card: <div onclick="location.href='/Stream/{slug}.html';">
_CARD_URL_RE = re.compile(r"/Stream/[^'\"]+")
_YEAR_RE = re.compile(r"\d{4}")


def _texts(node: LexborNode) -> Iterator[str]:
    """The text of each text node under *node*, in document order."""
    for child in node.traverse(include_text=True):
        if child.tag == "-text":
            yield child.text()


def _card_url(node: LexborNode) -> str:
    """The ``/Stream/`` path of a result card ("" for any other node)."""
    if node.tag != "div":
        return ""
    m = _CARD_URL_RE.search(node.attributes.get("onclick") or "")
    return m.group(0) if m else ""


def _is_year(node: LexborNode) -> bool:
    """Whether *node* is a ``<span class="Year">``."""
    return node.tag == "span" and "Year" in classes(node)


def _year(span: LexborNode) -> str:
    """The year of a Year span: the last of its texts with four digits."""
    years = [m.group(0) for text in _texts(span) if (m := _YEAR_RE.search(text))]
    return years[-1] if years else ""


def _title_text(heading: LexborNode) -> str:
    """The text of an h1 outside its Year spans."""
    return "".join(
        node.text()
        for node in heading.traverse(include_text=True)
        if node.tag == "-text"
        and not any(_is_year(parent) for parent in ancestors(node))
    )


class _SearchResultParser:
    """Parse search results from a kinox.to search page (selectolax).

    Each result is a ``<div onclick="location.href='/Stream/...'"``> with:
    - ``<a href="/Stream/{slug}.html"><h1>Title</h1></a>``
    - ``<div class="Genre">`` with genre links and IMDb rating

    A card inside another card belongs to the outer one.
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for card in tree.css("div[onclick*='/Stream/']"):
            url = _card_url(card)
            if url and not any(_card_url(parent) for parent in ancestors(card)):
                self._add_card(card, url)

    def _add_card(self, card: LexborNode, url: str) -> None:
        # The last h1 names the card
        headings = card.css("h1")
        title = headings[-1].text().strip() if headings else ""
        if not title:
            return
        # Each text of a genre link is a genre; the card's last text with
        # "/ 10" is its IMDb rating
        genres = [
            text.strip()
            for link in card.css("a[href*='/Genre/']")
            for text in _texts(link)
        ]
        ratings = [text.strip() for text in _texts(card) if "/ 10" in text]
        self.results.append(
            {
                "title": title,
                "url": url,
                "genre": ", ".join(genres),
                "imdb": ratings[-1] if ratings else "",
            }
        )


class _DetailPageParser:
    """Parse a kinox.to movie/series detail page (selectolax).

    Extracts:
    - Title from ``<h1><span>Title</span> <span class="Year">(YYYY)</span></h1>``
    - Year from ``<span class="Year">``
    - Hosters from ``<ul id="HosterList"><li id="Hoster_N">``
    - Series detection via ``<select id="SeasonSelection">``

    The title and year come from the first h1 with text outside its Year
    span and a year in it: the "Navigation" h1 before it has none, and the
    related entries further down carry Year spans of their own.
    """

    def __init__(self) -> None:
        self.title = ""
        self.year = ""
        self.hosters: list[dict[str, str]] = []
        self.is_series = False

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_title(tree)
        for item in tree.css("ul#HosterList li[id^='Hoster_']"):
            name = "".join(div.text() for div in item.css("div.Named")).strip()
            if name:
                hoster_id = (item.attributes.get("id") or "").replace("Hoster_", "")
                self.hosters.append({"name": name, "id": hoster_id})
        if tree.css_first("select#SeasonSelection") is not None:
            self.is_series = True

    def _read_title(self, tree: LexborHTMLParser) -> None:
        for heading in tree.css("h1"):
            title = _title_text(heading).strip()
            years = [year for span in heading.css("span.Year") if (year := _year(span))]
            if title and years:
                self.title = title
                self.year = years[-1]
                return


class KinoxPlugin(HttpxPluginBase):
    """Python plugin for kinox.to / kinos.to / kinoz.to using httpx."""

    name = "kinox"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(self, query: str) -> list[dict[str, str]]:
        """Fetch search page and parse results."""
        html = await self._fetch_text(
            f"{self.base_url}/Search.html", params={"q": query}, context="search"
        )
        if html is None:
            return []

        parser = await parse_page(_SearchResultParser(), html)

        self._log.info("kinox_search", query=query, count=len(parser.results))
        return parser.results

    async def _fetch_detail_page(self, url_path: str) -> _DetailPageParser:
        """Fetch a movie/series detail page and parse it."""
        html = await self._fetch_text(f"{self.base_url}{url_path}", context="detail")
        if html is None:
            return _DetailPageParser()

        parser = await parse_page(_DetailPageParser(), html)

        self._log.info(
            "kinox_detail",
            url=url_path,
            title=parser.title,
            year=parser.year,
            hosters=len(parser.hosters),
            is_series=parser.is_series,
        )
        return parser

    async def _fetch_mirror_url(self, slug: str, hoster_id: str) -> str | None:
        """Fetch embed iframe URL for a hoster mirror via AJAX.

        kinox.to serves embed URLs via:
        GET /aGET/Mirror/{slug}&Hoster={id}&Mirror=1
        which returns JSON ``{"Stream": "<iframe src=...>", ...}`` (older
        answers: the iframe HTML itself). The iframe points at the hoster or at
        kinox's own ``/redirect/<hash>`` (made absolute here, resolved later).
        """
        html = await self._fetch_text(
            f"{self.base_url}/aGET/Mirror/{slug}&Hoster={hoster_id}&Mirror=1",
            context="mirror",
        )
        if html is None:
            return None
        try:
            data = json.loads(html)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("Stream"), str):
            html = data["Stream"]
        m = re.search(r'<iframe[^>]+src=["\']([^"\']+)', html)
        return urljoin(f"{self.base_url}/", m.group(1).strip()) if m else None

    def _build_search_result(
        self,
        search_entry: dict[str, str],
        detail: _DetailPageParser,
        download_links: list[dict[str, str]] | None = None,
    ) -> SearchResult:
        """Build a SearchResult from search entry and detail page data."""
        title = detail.title or search_entry.get("title", "")
        year = detail.year
        url_path = search_entry.get("url", "")
        source_url = f"{self.base_url}{url_path}"

        display_title = f"{title} ({year})" if year else title
        category = 5000 if detail.is_series else 2000

        return SearchResult(
            title=display_title,
            download_link=download_links[0]["link"] if download_links else source_url,
            download_links=download_links or None,
            source_url=source_url,
            published_date=year or None,
            category=category,
        )

    async def _process_entry(
        self,
        entry: dict[str, str],
        sem: asyncio.Semaphore,
        mirror_sem: asyncio.Semaphore,
        category: int | None,
    ) -> SearchResult | None:
        """Fetch detail page for one search entry, then fetch mirror URLs.

        *mirror_sem* is shared by all entries of a search, so the mirror
        AJAX calls stay within the plugin's concurrency limit.
        """
        url_path = entry.get("url", "")
        if not url_path:
            return None

        async with sem:
            detail = await self._fetch_detail_page(url_path)

        # Extract slug: "/Stream/Batman_Begins.html" → "Batman_Begins"
        slug = url_path.replace("/Stream/", "").replace(".html", "")

        # Fetch embed URLs for each hoster (bounded concurrency)
        links: list[dict[str, str]] = []
        if detail.hosters:

            async def _fetch(h: dict[str, str]) -> dict[str, str] | None:
                async with mirror_sem:
                    embed_url = await self._fetch_mirror_url(slug, h["id"])
                    if embed_url:
                        return {"hoster": h["name"], "link": embed_url}
                    return None

            results = await asyncio.gather(*[_fetch(h) for h in detail.hosters])
            links = [r for r in results if isinstance(r, dict)]
            # kinox's own /redirect/<hash> links → hoster URL (or dropped)
            links = await self._resolve_own_links(links)

        if not links:
            # the kinox page itself is no download link
            self._log.info("kinox_no_hoster_links", url=url_path)
            return None
        sr = self._build_search_result(entry, detail, download_links=links)

        # Post-filter by category range
        if category is not None:
            cat_range = (category // 1000) * 1000
            if not (cat_range <= sr.category < cat_range + 1000):
                return None

        return sr

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search kinox.to and return results.

        Uses the search page to find movies/series, then fetches detail
        pages to extract year, hosters, and content type.
        A season request gets nothing without a request: kinox's mirror API
        only serves a series page's default episode, and films are another
        category.
        """
        if not query or season is not None:
            return []

        # Accept movies (2xxx), TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        await self._ensure_client()
        await self._verify_domain()

        search_entries = relevant_hits(await self._search_page(query), query, hit_title)
        if not search_entries:
            return []

        sem = self._new_semaphore()
        mirror_sem = self._new_semaphore()
        tasks = [
            self._process_entry(e, sem, mirror_sem, category) for e in search_entries
        ]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results[: self.effective_max_results]


plugin = KinoxPlugin()
