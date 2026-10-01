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
from html.parser import HTMLParser
from urllib.parse import urljoin

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins import devideosrc
from scavengarr.infrastructure.plugins.categories import (
    STREAM_CATEGORIES,
    filter_by_category,
    served_category,
    stream_category,
)
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


class _SearchResultParser(HTMLParser):
    """Parse hdfilme.cafe search result cards.

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
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

        # Item tracking
        self._in_item = False
        self._item_div_depth = 0

        # Title link
        self._in_movie_title_a = False
        self._current_title = ""
        self._current_url = ""

        # Meta spans (year, duration, quality)
        self._in_meta = False
        self._in_meta_span = False
        self._meta_span_text = ""
        self._meta_spans: list[str] = []

    def _reset_item(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._meta_spans = []

    def _emit_item(self) -> None:
        if not self._current_title or not self._current_url:
            return

        year = ""
        duration = ""
        quality = ""
        for span in self._meta_spans:
            text = span.strip()
            if re.match(r"^\d{4}$", text):
                year = text
            elif "min" in text.lower():
                duration = text
            elif text.upper() in ("HD", "CAM", "TS", "SD", "4K"):
                quality = text

        self.results.append(
            {
                "title": self._current_title,
                "url": self._current_url,
                "year": year,
                "duration": duration,
                "quality": quality,
            }
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Item boundary: <div class="item ...">
        if tag == "div":
            if self._in_item:
                self._item_div_depth += 1
            elif "item" in classes:
                self._in_item = True
                self._item_div_depth = 0
                self._reset_item()

        if not self._in_item:
            return

        # <a class="movie-title" ...>
        if tag == "a" and "movie-title" in classes:
            self._in_movie_title_a = True
            href = attr_dict.get("href", "") or ""
            if href:
                self._current_url = urljoin(self._base_url, href)
            self._current_title = ""

        # <div class="meta ...">
        if tag == "div" and "meta" in classes:
            self._in_meta = True

        # <span> inside meta div
        if tag == "span" and self._in_meta:
            self._in_meta_span = True
            self._meta_span_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_movie_title_a:
            self._current_title += data

        if self._in_meta_span:
            self._meta_span_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_movie_title_a:
            self._in_movie_title_a = False
            self._current_title = self._current_title.strip()

        if tag == "span" and self._in_meta_span:
            self._in_meta_span = False
            text = self._meta_span_text.strip()
            if text:
                self._meta_spans.append(text)

        if tag == "div":
            if self._in_meta:
                self._in_meta = False
            if self._in_item:
                if self._item_div_depth > 0:
                    self._item_div_depth -= 1
                else:
                    self._in_item = False
                    self._emit_item()


class _DetailPageParser(HTMLParser):
    """Parse hdfilme.cafe film/series detail page.

    Stream links are not on the page; they come from the embedded
    devideosrc player.

    Extracts:
    - Genres from ``<a href="/{genre}/">GenreName</a>`` in info section
    - Year, duration, quality from metadata spans
    - TMDB URL from ``<a href="themoviedb.org/...">``
    - IMDb URL from ``<a href="imdb.com/title/...">``
    - Description from h2 heading (distinguishes film/series)
    - Series detection from ``Staffel/Episode:`` in metadata or /serien/ genre
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Metadata
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

        # Info section tracking
        self._in_info = False
        self._info_div_depth = 0
        self._in_genre_span = False
        self._in_genre_a = False
        self._genre_a_href = ""
        self._genre_a_text = ""
        self._in_meta_line = False
        self._meta_line_div_depth = 0
        self._in_meta_span = False
        self._meta_span_text = ""
        self._meta_spans: list[str] = []

        # H1 tracking
        self._in_h1 = False
        self._h1_text = ""

        # H2 tracking (series detection)
        self._in_h2 = False
        self._h2_text = ""

        # Description
        self._in_prose = False
        self._prose_text = ""
        self._in_prose_a = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        href = attr_dict.get("href", "") or ""

        # h1
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # h2
        if tag == "h2":
            self._in_h2 = True
            self._h2_text = ""

        # Info section: <div class="info md:pl-5 md:flex-grow">
        if tag == "div" and "info" in classes:
            self._in_info = True
            self._info_div_depth = 0
        elif tag == "div" and self._in_info:
            self._info_div_depth += 1

        # Genre span (first span in meta line, contains genre links)
        if self._in_info and tag == "div" and "border-b" in classes:
            self._in_meta_line = True
            self._meta_line_div_depth = 0
        elif tag == "div" and self._in_meta_line:
            self._meta_line_div_depth += 1

        # Track first span in meta line for genres
        if self._in_meta_line and tag == "span" and not self._in_genre_span:
            # Check if this is a divider span
            if "divider" not in classes and "align-text-bottom" not in classes:
                if not self.genres and not self._in_genre_span:
                    self._in_genre_span = True

        # Genre links inside the genre span
        if self._in_genre_span and tag == "a":
            self._in_genre_a = True
            self._genre_a_href = href
            self._genre_a_text = ""

        # Meta spans for year/duration/quality
        if self._in_meta_line and tag == "span":
            self._in_meta_span = True
            self._meta_span_text = ""

        # TMDB link
        if tag == "a" and "themoviedb.org" in href:
            self.tmdb_url = href
            if "/tv/" in href:
                self.is_series = True

        # IMDb link
        if tag == "a" and "imdb.com/title/" in href:
            self.imdb_url = href
            m = re.search(r"title/(tt\d+)", href)
            if m and not self.imdb_id:
                self.imdb_id = m.group(1)

        # Description prose
        if tag == "div" and "prose" in classes:
            self._in_prose = True
            self._prose_text = ""

        if self._in_prose and tag == "a":
            self._in_prose_a = True

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data

        if self._in_h2:
            self._h2_text += data

        if self._in_genre_a:
            self._genre_a_text += data

        if self._in_meta_span:
            self._meta_span_text += data

        if self._in_prose and not self._in_prose_a:
            self._prose_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = self._h1_text.strip()
            # Remove " hdfilme" suffix
            if self.title.lower().endswith(" hdfilme"):
                self.title = self.title[: -len(" hdfilme")].strip()

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            h2 = self._h2_text.strip().lower()
            if "stream serien" in h2 or "serien kostenlos" in h2:
                self.is_series = True

        if tag == "a" and self._in_genre_a:
            self._in_genre_a = False
            genre_text = self._genre_a_text.strip()
            genre_href = self._genre_a_href
            if genre_text:
                self.genres.append(genre_text)
                if "/serien/" in genre_href:
                    self.is_series = True

        if tag == "span" and self._in_genre_span:
            # The first span ends with the divider
            pass

        if tag == "span" and self._in_meta_span:
            self._in_meta_span = False
            text = self._meta_span_text.strip()
            if text:
                self._meta_spans.append(text)
                if re.match(r"^\d{4}$", text):
                    self.year = text
                elif "min" in text.lower():
                    self.duration = text
                elif "Staffel/Episode:" in text or "Staffel" in text:
                    self.is_series = True
                elif text.upper() in ("HD", "CAM", "TS", "SD", "4K", "HD/DEUTSCH"):
                    self.quality = text

        if tag == "div" and self._in_meta_line:
            if self._meta_line_div_depth > 0:
                self._meta_line_div_depth -= 1
            else:
                self._in_meta_line = False
                self._in_genre_span = False

        if tag == "div" and self._in_info:
            if self._info_div_depth > 0:
                self._info_div_depth -= 1
            else:
                self._in_info = False

        if tag == "a" and self._in_prose_a:
            self._in_prose_a = False

        if tag == "div" and self._in_prose:
            self._in_prose = False
            self.description = self._prose_text.strip()


class HdfilmePlugin(HttpxPluginBase):
    """Python plugin for hdfilme.cafe using httpx."""

    name = "hdfilme"
    provides = "stream"
    _domains = _DOMAINS

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

        parser = _SearchResultParser(self.base_url)
        parser.feed(html)

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

        parser = _SearchResultParser(self.base_url)
        parser.feed(html)

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

        parser = _DetailPageParser(self.base_url)
        parser.feed(html)

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
            links = devideosrc.filter_episodes(links, season, episode)

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
