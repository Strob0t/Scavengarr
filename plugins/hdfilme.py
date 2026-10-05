"""hdfilme.cafe Python plugin for Scavengarr.

Scrapes hdfilme.cafe (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML search + detail pages)
- GET /?story={query}&do=search&subaction=search for keyword search
- Detail page scraping for metadata (genres, year, duration, IMDb, TMDB)
- Stream links from the embedded devideosrc.co player (movies and series,
  see ``scavengarr.infrastructure.plugins.devideosrc``)
- Category detection from detail page: /serien/ genre link → TV (5000)
- Bounded concurrency for detail page scraping

Domain: hdfilme.cafe (2026-09-28: hdfilme.legal → .press → .party → .bid all
redirect here).
No authentication required.

Known upstream breakage (2026-09-28): the site's own keyword search answers
with a PHP fatal error (``engine/mods/sfilter/filter.php``); browsing a
category (empty query) works.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins import devideosrc
from scavengarr.infrastructure.plugins.categories import (
    STREAM_CATEGORIES,
    filter_by_category,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
from scavengarr.infrastructure.plugins.episodes import filter_episodes
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["hdfilme.cafe"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Torznab category → site category path for browsing.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "filme1",
    5000: "serien",
}


class _SearchResultParser:
    """Parse hdfilme.cafe search result cards (selectolax).

    Each result card has this structure::

        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="block relative" href="/filme1/{id}-{slug}-stream.html"
               title="Title" data-tooltip-id="...">
              <figure>...</figure>
            </a>
            <a class="movie-title" title="Title"
               href="/filme1/{id}-{slug}-stream.html">
              <h3 class="..."> Title </h3>
            </a>
            <p class="..."> Title </p>
            <div class="...">
              <div class="meta ...">
                <span>2004</span>
                <i class="dot ..."></i>
                <span>20 min</span>
                <span class="... right-0 ..."> HD </span>
              </div>
            </div>
          </div>
        </div>

    A card's title is the text of its last ``movie-title`` link, its URL
    the last such link with an ``href``.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        for item in LexborHTMLParser(html).css("div.item"):
            if not _nested_item(item):
                self._add_item(item)

    def _add_item(self, item: LexborNode) -> None:
        title = url = ""
        for link in item.css("a.movie-title"):
            href = link.attributes.get("href") or ""
            if href:
                url = urljoin(self._base_url, href)
            title = link.text().strip()
        if not title or not url:
            return

        year = ""
        duration = ""
        quality = ""
        for span in item.css("div.meta span"):
            text = span.text().strip()
            if re.match(r"^\d{4}$", text):
                year = text
            elif "min" in text.lower():
                duration = text
            elif text.upper() in ("HD", "CAM", "TS", "SD", "4K"):
                quality = text

        self.results.append(
            {
                "title": title,
                "url": url,
                "year": year,
                "duration": duration,
                "quality": quality,
            }
        )


def _nested_item(node: LexborNode) -> bool:
    """Whether *node* lies inside another result card."""
    return any(
        parent.tag == "div" and "item" in classes(parent) for parent in ancestors(node)
    )


class _DetailPageParser:
    """Parse hdfilme.cafe film/series detail page (selectolax).

    Stream links are not on the page; they come from the embedded
    devideosrc player.

    The info section's meta line::

        <div class="info md:pl-5 md:flex-grow">
          <h1 class="font-bold ...">Title</h1>
          <div class="border-b border-gray-700 ...">
            <span><a href="/drama/">Drama</a>&nbsp;<a href="/krieg/">Krieg</a></span>
            <span class="align-text-bottom divider ...">|</span>
            <span><a href="/xfsearch/country/usa/">USA</a></span>
            <span class="align-text-bottom divider ...">|</span>
            <span>2023</span>
            ...
          </div>
        </div>

    Extracts:
    - Genres from ``<a href="/{genre}/">GenreName</a>`` in info section
    - Year, duration, quality from metadata spans
    - TMDB URL from ``<a href="themoviedb.org/...">``
    - IMDb URL from ``<a href="imdb.com/title/...">``
    - Description from the ``prose`` div, without its links' text
    - Series detection from ``Staffel/Episode:`` in metadata, a /serien/
      genre, a TMDB ``/tv/`` link or the h2 heading
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.imdb_id = ""
        self.tmdb_url = ""
        self.imdb_url = ""
        self.genres: list[str] = []
        self.year = ""
        self.duration = ""
        self.quality = ""
        self.is_series = False
        self.title = ""
        self.description = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_headings(tree)
        for line in tree.css("div.info div.border-b"):
            self._read_meta_line(line)
        self._read_links(tree)
        # The last description wins
        for prose in tree.css("div.prose"):
            self.description = _text_outside_links(prose).strip()

    def _read_headings(self, tree: LexborHTMLParser) -> None:
        # The last h1 names the title
        for h1 in tree.css("h1"):
            self.title = h1.text().strip()
            # Remove " hdfilme" suffix
            if self.title.lower().endswith(" hdfilme"):
                self.title = self.title[: -len(" hdfilme")].strip()
        for h2 in tree.css("h2"):
            heading = h2.text().strip().lower()
            if "stream serien" in heading or "serien kostenlos" in heading:
                self.is_series = True

    def _read_links(self, tree: LexborHTMLParser) -> None:
        """TMDB and IMDb links: the last URL wins, the first IMDb id."""
        for link in tree.css("a[href*='themoviedb.org']"):
            self.tmdb_url = link.attributes.get("href") or ""
            if "/tv/" in self.tmdb_url:
                self.is_series = True
        for link in tree.css("a[href*='imdb.com/title/']"):
            self.imdb_url = link.attributes.get("href") or ""
            m = re.search(r"title/(tt\d+)", self.imdb_url)
            if m and not self.imdb_id:
                self.imdb_id = m.group(1)

    def _read_meta_line(self, line: LexborNode) -> None:
        """Genres, year, duration and quality from a meta line's spans.

        Every link from the first non-divider span on is a genre (the
        country links too), unless an earlier meta line had genres.
        """
        in_genres = False
        for node in line.css("span, a"):
            if node.tag == "a":
                if in_genres:
                    self._add_genre(node)
                continue
            if not in_genres and not self.genres and not _is_divider(node):
                in_genres = True
            self._read_meta_span(node.text().strip())

    def _add_genre(self, link: LexborNode) -> None:
        genre = link.text().strip()
        if genre:
            self.genres.append(genre)
            if "/serien/" in (link.attributes.get("href") or ""):
                self.is_series = True

    def _read_meta_span(self, text: str) -> None:
        if not text:
            return
        if re.match(r"^\d{4}$", text):
            self.year = text
        elif "min" in text.lower():
            self.duration = text
        elif "Staffel" in text:  # "Staffel/Episode: 5x08"
            self.is_series = True
        elif text.upper() in ("HD", "CAM", "TS", "SD", "4K", "HD/DEUTSCH"):
            self.quality = text


def _is_divider(span: LexborNode) -> bool:
    """Whether *span* is a ``|`` divider of the meta line."""
    names = classes(span)
    return "divider" in names or "align-text-bottom" in names


def _text_outside_links(node: LexborNode) -> str:
    """The text of *node* without the text of its links."""
    parts: list[str] = []
    for child in node.iter(include_text=True):
        if child.is_text_node:
            parts.append(child.text_content or "")
        elif child.is_element_node and child.tag != "a":
            parts.append(_text_outside_links(child))
    return "".join(parts)


class HdfilmePlugin(HttpxPluginBase):
    """Python plugin for hdfilme.cafe using httpx."""

    name = "hdfilme"
    provides = "stream"
    _domains = _DOMAINS
    # One database behind hdfilme, streamcloud and streamkiste (same news ids)
    mirror_group = "hdfilme"

    async def _search_page(self, query: str) -> list[dict[str, str]]:
        """Fetch search results page.

        Search uses GET with DLE CMS parameters:
        ``/?story={query}&do=search&subaction=search``

        Pagination is JS-based (only first page fetchable via httpx).
        Returns up to ~24 results per page.
        """
        html = await self._fetch_text(
            self.base_url,
            params={"story": query, "do": "search", "subaction": "search"},
            context="search",
        )
        if html is None:
            return []

        parser = await parse_page(_SearchResultParser(self.base_url), html)

        self._log.info(
            "hdfilme_search_results",
            query=query,
            results=len(parser.results),
        )
        return parser.results

    async def _browse_page(
        self,
        path: str,
        page_num: int = 1,
    ) -> list[dict[str, str]]:
        """Fetch a browse/category listing page.

        Pages use ``/{path}/page/{n}/`` URL pattern.
        """
        if page_num > 1:
            url = f"{self.base_url}/{path}/page/{page_num}/"
        else:
            url = f"{self.base_url}/{path}/"

        html = await self._fetch_text(url, context="browse")
        if html is None:
            return []

        parser = await parse_page(_SearchResultParser(self.base_url), html)

        self._log.info(
            "hdfilme_browse_page",
            path=path,
            page=page_num,
            count=len(parser.results),
        )
        return parser.results

    async def _browse_category(
        self,
        path: str,
    ) -> list[dict[str, str]]:
        """Browse a category with pagination up to max_results items.

        Pages contain ~24 items each. 1000/24 ≈ 42 pages max.
        """
        max_pages = 42
        all_results: list[dict[str, str]] = []

        for page_num in range(1, max_pages + 1):
            results = await self._browse_page(path, page_num)
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= self.effective_max_results:
                break

        return all_results[: self.effective_max_results]

    async def _scrape_detail(
        self,
        result: dict[str, str],
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Scrape a film/series detail page and load its devideosrc links.

        Series links are filtered to the requested *season* / *episode*.
        """
        detail_url = result["url"]
        html = await self._fetch_text(detail_url, context="detail")
        if html is None:
            return []

        parser = await parse_page(_DetailPageParser(self.base_url), html)

        player = devideosrc.find_player(html)
        if player is None:
            self._log.debug("hdfilme_no_player", url=detail_url)
            return []

        client = await self._ensure_client()
        found = await devideosrc.fetch_links(
            client, player, **self._request_kwargs(client)
        )
        links = found.links
        is_series = found.kind == "tv" or parser.is_series

        if season is not None:
            if not is_series:
                return []  # films are skipped when season/episode are requested
            links = filter_episodes(links, season, episode)

        if not links:
            self._log.debug("hdfilme_no_streams", url=detail_url)
            return []

        title = parser.title or result.get("title", "")
        year = parser.year or result.get("year", "")
        genres = ", ".join(parser.genres) if parser.genres else ""

        category = stream_category(parser.genres, is_series=is_series)

        metadata = {
            "year": year,
            "genres": genres,
            "quality": parser.quality or result.get("quality", ""),
            "duration": parser.duration or result.get("duration", ""),
            "imdb_id": parser.imdb_id or player.imdb_id,
            "imdb_url": parser.imdb_url,
            "tmdb_url": parser.tmdb_url,
        }
        description = f"{genres} ({year})" if genres and year else genres or year

        return [
            SearchResult(
                title=title,
                download_link=links[0]["link"],
                download_links=links,
                source_url=detail_url,
                category=category,
                description=description,
                metadata=metadata,
            )
        ]

    async def _scrape_all_details(
        self,
        items: list[dict[str, str]],
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Scrape detail pages with bounded concurrency."""
        sem = self._new_semaphore()

        async def _bounded(r: dict[str, str]) -> list[SearchResult]:
            async with sem:
                return await self._scrape_detail(r, season=season, episode=episode)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in items],
            return_exceptions=True,
        )

        results: list[SearchResult] = []
        for item in gathered:
            if isinstance(item, list):
                for sr in item:
                    if isinstance(sr, SearchResult):
                        results.append(sr)
        return results

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search hdfilme.cafe and return results with stream links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, STREAM_CATEGORIES)
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()

        if query:
            # Each hit costs a detail page and its player: scrape real matches only
            all_items = relevant_hits(
                await self._search_page(query),
                query,
                hit_title,
                limit=SINGLE_TITLE_HITS if season is not None else None,
            )
        elif category is not None:
            path = _CATEGORY_PATH_MAP[category - category % 1000]
            all_items = await self._browse_category(path)
        else:
            return []

        if not all_items:
            return []

        all_items = all_items[: self.effective_max_results]
        results = await self._scrape_all_details(
            all_items, season=season, episode=episode
        )

        if category is not None:
            results = filter_by_category(results, category)

        return results


plugin = HdfilmePlugin()
