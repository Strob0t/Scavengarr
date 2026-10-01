"""streamcloud Python plugin for Scavengarr.

Scrapes streamcloud.download (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /?do=search&subaction=search&story={query} for keyword search
- POST-based pagination via search_start={N}&result_from={offset}
  (12 results/page, up to 84 pages for ~1000 results)
- Detail page scraping for metadata; stream/hoster links come from the
  embedded devideosrc player (movie or series, see
  ``scavengarr.infrastructure.plugins.devideosrc``)
- Series detection from the player kind and the "Serien" genre
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Multi-domain support: streamcloud.download (primary; streamcloud.plus and
streamcloud.uno redirect there), streamcloud.my (fallback).
No authentication required.
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
from scavengarr.infrastructure.plugins.relevance import hit_title, relevant_hits

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["streamcloud.download", "streamcloud.my"]
_RESULTS_PER_PAGE = 12
_MAX_PAGES = 84  # 12 results/page → 84 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Genres that indicate a series
_SERIES_GENRES = frozenset({"serie", "serien"})

# German genre → Torznab-like genre mapping for reference
_GENRE_MAP: dict[str, str] = {
    "action": "Action",
    "abenteuer": "Adventure",
    "animation": "Animation",
    "biographie": "Biography",
    "dokumentation": "Documentary",
    "drama": "Drama",
    "familie": "Family",
    "fantasy": "Fantasy",
    "historie": "History",
    "horror": "Horror",
    "komödie": "Comedy",
    "komodie": "Comedy",
    "krieg": "War",
    "krimi": "Crime",
    "musik": "Music",
    "mystery": "Mystery",
    "romantik": "Romance",
    "sci-fi": "Sci-Fi",
    "sport": "Sport",
    "thriller": "Thriller",
    "western": "Western",
    "liebesfilm": "Romance",
    "reality-tv": "Reality-TV",
    "serien": "TV Series",
    "serie": "TV Series",
}


def _detect_series(genres: list[str]) -> bool:
    """Detect if an item is a series based on genres."""
    lower_genres = {g.lower() for g in genres}
    return bool(lower_genres & _SERIES_GENRES)


def _clean_title(title: str) -> str:
    """Strip common suffixes and trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


class _SearchResultParser(HTMLParser):
    """Parse streamcloud.plus DLE search result page.

    Each result is a card with structure::

        <div class="item cf item-video ...">
          <div class="thumb" title="Title">
            <a href="https://streamcloud.plus/12345-title-stream-deutsch.html">
              <img src="..." alt="Title">
            </a>
          </div>
          <div class="f_title">
            <a href="...">Title</a>
          </div>
          <div class="f_year">2024</div>
        </div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

        # Card tracking
        self._in_card = False
        self._card_div_depth = 0

        # Thumb link (detail URL)
        self._in_thumb = False
        self._thumb_div_depth = 0
        self._current_url = ""
        self._thumb_title = ""

        # Title tracking
        self._in_f_title = False
        self._f_title_div_depth = 0
        self._in_title_a = False
        self._current_title = ""

        # Year tracking
        self._in_f_year = False
        self._f_year_div_depth = 0
        self._current_year = ""

    def _reset_card(self) -> None:
        self._current_url = ""
        self._current_title = ""
        self._current_year = ""
        self._thumb_title = ""

    def _emit_card(self) -> None:
        title = self._current_title or self._thumb_title
        if not title or not self._current_url:
            return

        self.results.append(
            {
                "title": _clean_title(title),
                "url": self._current_url,
                "year": self._current_year.strip(),
            }
        )

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Card boundary: <div class="item cf item-video ...">
        if tag == "div":
            if self._in_card:
                self._card_div_depth += 1

                # Thumb area
                if self._in_thumb:
                    self._thumb_div_depth += 1
                elif "thumb" in classes:
                    self._in_thumb = True
                    self._thumb_div_depth = 0
                    self._thumb_title = attr_dict.get("title", "") or ""

                # Title area
                if self._in_f_title:
                    self._f_title_div_depth += 1
                elif "f_title" in classes:
                    self._in_f_title = True
                    self._f_title_div_depth = 0

                # Year area
                if self._in_f_year:
                    self._f_year_div_depth += 1
                elif "f_year" in classes:
                    self._in_f_year = True
                    self._f_year_div_depth = 0
                    self._current_year = ""

            elif "item" in classes and "cf" in classes:
                self._in_card = True
                self._card_div_depth = 0
                self._reset_card()

        if not self._in_card:
            return

        # Link inside thumb (detail URL)
        if tag == "a" and self._in_thumb:
            href = attr_dict.get("href", "") or ""
            if href:
                self._current_url = urljoin(self._base_url, href)

        # Title link: <a> inside f_title div
        if tag == "a" and self._in_f_title:
            self._in_title_a = True
            self._current_title = ""
            href = attr_dict.get("href", "") or ""
            if href and not self._current_url:
                self._current_url = urljoin(self._base_url, href)

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data

        if self._in_f_year:
            self._current_year += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "a" and self._in_title_a:
            self._in_title_a = False
            self._current_title = self._current_title.strip()

        if tag == "div":
            if self._in_f_year:
                if self._f_year_div_depth > 0:
                    self._f_year_div_depth -= 1
                else:
                    self._in_f_year = False

            if self._in_f_title:
                if self._f_title_div_depth > 0:
                    self._f_title_div_depth -= 1
                else:
                    self._in_f_title = False

            if self._in_thumb:
                if self._thumb_div_depth > 0:
                    self._thumb_div_depth -= 1
                else:
                    self._in_thumb = False

            if self._in_card:
                if self._card_div_depth > 0:
                    self._card_div_depth -= 1
                else:
                    self._in_card = False
                    self._emit_card()


class _DetailPageParser(HTMLParser):
    """Parse streamcloud detail page metadata.

    Stream links are not on the page itself; they come from the embedded
    devideosrc player (see ``scavengarr.infrastructure.plugins.devideosrc``).

    Metadata fields (value in a ``<div>`` or ``<span>`` after the label)::

        <strong>Genres: </strong> <div>Serien / Krimi / Drama</div>
        <strong>Veröffentlicht: </strong> <div><a href="/xfsearch/2008">2008</a></div>
        <strong>Spielzeit: </strong> <div>50 min</div>
        IMDb link: <a href="https://www.imdb.com/title/ttXXXXX/">6.1/10</a>
    """

    _VALUE_TAGS = ("span", "div")

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Metadata
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""
        self.imdb_id = ""
        self.runtime = ""

        # Description tracking
        self._in_desc_p = False
        self._desc_text = ""

        # Metadata field tracking: value element after a <strong> label
        self._last_strong_text = ""
        self._in_strong = False
        self._value_field = ""  # "genres" | "runtime" while inside the value
        self._value_tag = ""
        self._value_depth = 0
        self._value_text = ""
        self._in_year_a = False
        self._year_text = ""

        # IMDb link tracking
        self._in_imdb_a = False
        self._imdb_text = ""

    @property
    def is_series(self) -> bool:
        return _detect_series(self.genres)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if self._value_field and tag == self._value_tag:
            self._value_depth += 1

        if tag == "strong":
            self._in_strong = True
            self._last_strong_text = ""

        # Value element right after "Genres:" / "Spielzeit:"
        if tag in self._VALUE_TAGS and not self._value_field:
            field = {"Genres:": "genres", "Spielzeit:": "runtime"}.get(
                self._last_strong_text
            )
            if field:
                self._value_field = field
                self._value_tag = tag
                self._value_depth = 1
                self._value_text = ""
                self._last_strong_text = ""

        if tag == "a":
            href = attr_dict.get("href", "") or ""
            # Year link: <a href="/xfsearch/2008">
            if re.search(r"/xfsearch/\d{4}$", href):
                self._in_year_a = True
                self._year_text = ""

            # IMDb link
            if "imdb.com/title/" in href:
                self._in_imdb_a = True
                self._imdb_text = ""
                m = re.search(r"(tt\d+)", href)
                if m:
                    self.imdb_id = m.group(1)

        # Description paragraph (first <p> inside the detail info area)
        if tag == "p" and not self._in_desc_p and not self.description:
            self._in_desc_p = True
            self._desc_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_strong:
            self._last_strong_text += data
        if self._value_field:
            self._value_text += data
        if self._in_year_a:
            self._year_text += data
        if self._in_imdb_a:
            self._imdb_text += data
        if self._in_desc_p:
            self._desc_text += data

    def _end_value(self) -> None:
        """Store the label value that just closed."""
        raw = " ".join(self._value_text.split())
        if self._value_field == "genres":
            self.genres = [g.strip() for g in raw.split("/") if g.strip()]
        else:
            self.runtime = raw
        self._value_field = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong" and self._in_strong:
            self._in_strong = False
            self._last_strong_text = self._last_strong_text.strip()

        if self._value_field and tag == self._value_tag:
            self._value_depth -= 1
            if not self._value_depth:
                self._end_value()

        if tag == "a" and self._in_year_a:
            self._in_year_a = False
            text = self._year_text.strip()
            if re.match(r"\d{4}$", text):
                self.year = text

        if tag == "a" and self._in_imdb_a:
            self._in_imdb_a = False
            m = re.search(r"(\d+\.?\d*)/10", self._imdb_text.strip())
            if m:
                self.imdb_rating = m.group(1)

        if tag == "p" and self._in_desc_p:
            self._in_desc_p = False
            text = self._desc_text.strip()
            if len(text) > 20:
                self.description = text


class StreamcloudPlugin(HttpxPluginBase):
    """Python plugin for streamcloud.plus using httpx."""

    name = "streamcloud"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        page: int = 1,
    ) -> list[dict[str, str]]:
        """Fetch a search results page.

        DLE CMS search uses GET on page 1::
            GET /?do=search&subaction=search&story={query}

        Pagination uses POST to /index.php?do=search::
            POST with form data: do=search, subaction=search,
            search_start={page}, result_from={(page-1)*12+1}, story={query}
        """
        if page == 1:
            params: dict[str, str] = {
                "do": "search",
                "subaction": "search",
                "story": query,
            }
            html = await self._fetch_text(
                self.base_url, params=params, context="search"
            )
        else:
            form_data = {
                "do": "search",
                "subaction": "search",
                "search_start": str(page),
                "full_search": "0",
                "result_from": str((page - 1) * _RESULTS_PER_PAGE + 1),
                "story": query,
            }
            resp = await self._safe_fetch(
                f"{self.base_url}/index.php?do=search",
                method="POST",
                context="search",
                data=form_data,
            )
            html = resp.text if resp is not None else None
        if html is None:
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(html)

        self._log.info(
            "streamcloud_search_page",
            query=query,
            page=page,
            results=len(parser.results),
        )
        return parser.results

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str]]:
        """Fetch search results with pagination up to _max_results."""
        all_results: list[dict[str, str]] = []

        for page_num in range(1, _MAX_PAGES + 1):
            results = await self._search_page(query, page_num)
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
    ) -> SearchResult | None:
        """Scrape a detail page for metadata, then load its devideosrc links."""
        detail_url = result["url"]
        html = await self._fetch_text(detail_url, context="detail")
        if html is None:
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(html)

        player = devideosrc.find_player(html)
        if player is None:
            self._log.debug("streamcloud_no_player", url=detail_url)
            return None
        client = await self._ensure_client()
        found = await devideosrc.fetch_links(
            client, player, **self._request_kwargs(client)
        )
        links = found.links
        is_series = found.kind == "tv" or parser.is_series

        # Filter series links to requested season/episode
        if is_series and season is not None and links:
            links = devideosrc.filter_episodes(links, season, episode)
            if not links:
                self._log.debug(
                    "streamcloud_no_episode_match",
                    url=detail_url,
                    season=season,
                    episode=episode,
                )
                return None

        if not links:
            self._log.debug("streamcloud_no_streams", url=detail_url)
            return None

        title = _clean_title(result.get("title", ""))
        year = parser.year or result.get("year", "")
        genres = parser.genres
        category = stream_category(genres, is_series=is_series)

        description_parts: list[str] = []
        if genres:
            description_parts.append(", ".join(genres))
        if year:
            description_parts.append(f"({year})")
        if parser.description:
            description_parts.append(parser.description)
        description = " ".join(description_parts) if description_parts else ""

        metadata: dict[str, str] = {
            "year": year,
            "genres": ", ".join(genres),
            "imdb_rating": parser.imdb_rating,
            "imdb_id": parser.imdb_id or player.imdb_id,
            "runtime": parser.runtime,
        }

        return SearchResult(
            title=title,
            download_link=links[0]["link"],
            download_links=links,
            source_url=detail_url,
            category=category,
            description=description,
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search streamcloud.plus and return results with stream links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, STREAM_CATEGORIES)
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        # Each hit costs a detail page and its player: scrape real matches only
        all_items = relevant_hits(await self._search_all_pages(query), query, hit_title)
        if not all_items:
            return []

        # Scrape detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(r: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r, season=season, episode=episode)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in all_items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        if category is not None:
            results = filter_by_category(results, category)

        return results


plugin = StreamcloudPlugin()
