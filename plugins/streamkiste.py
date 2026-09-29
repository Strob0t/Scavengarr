"""streamkiste Python plugin for Scavengarr.

Scrapes streamkiste.bid (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /index.php?do=search&subaction=search&story={query} for page 1
- POST /index.php?do=search for page 2+ with form data
- 21 results per page, up to 48 pages for ~1000 results
- Detail page scraping for metadata; stream/hoster links come from the
  embedded devideosrc player (``scavengarr.infrastructure.plugins.devideosrc``)
- Series detection from the player answer and the "Serien" genre; the site
  embeds the series player for movies too
- Season/episode filtering of series links
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Multi-domain support: streamkiste.bid (primary; .taxi redirects there), .taxi,
.tv, .sx, .al, .city.
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

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "streamkiste.bid",
    "streamkiste.taxi",
    "streamkiste.tv",
    "streamkiste.sx",
    "streamkiste.al",
    "streamkiste.city",
]
_RESULTS_PER_PAGE = 21
_MAX_PAGES = 48  # 21 results/page → 48 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERIES_KEYWORDS = frozenset({"serie", "serien"})


def _detect_series(genres: list[str]) -> bool:
    """Detect if an item is a series based on genres."""
    lower_genres = {g.lower() for g in genres}
    return bool(lower_genres & _SERIES_KEYWORDS)


def _clean_title(title: str) -> str:
    """Strip common suffixes and trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


def _parse_release_text(text: str) -> tuple[str, list[str]]:
    """Parse release text like '2025 - Action Komödie Krimi kinofilme'.

    Returns (year, genres) where genres excludes 'kinofilme'.
    """
    text = text.strip()
    year = ""
    genres: list[str] = []

    m = re.match(r"(\d{4})\s*-?\s*(.*)", text)
    if m:
        year = m.group(1)
        rest = m.group(2).strip()
    else:
        rest = text

    if rest:
        for word in rest.split():
            word = word.strip()
            if word and word.lower() != "kinofilme":
                genres.append(word)

    return year, genres


class _SearchResultParser(HTMLParser):
    """Parse streamkiste.taxi DLE search result page.

    Each result is a card with structure like::

        <div class="movie-preview res_item">
          <span class="movie-title">   (or <div class="movie-title">)
            <a href="/film/12345-title.html" title="Title">Title</a>
          </span>
          <div class="movie-release">2025 - Action Komödie kinofilme</div>
          <div class="ico-bar">
            <span class="icon-hd"></span>
          </div>
        </div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

        self._in_card = False
        self._card_div_depth = 0

        self._in_movie_title_div = False
        self._movie_title_tag = ""
        self._movie_title_div_depth = 0
        self._in_title_a = False
        self._current_title = ""
        self._current_url = ""

        self._in_release_div = False
        self._release_text = ""

        self._quality_badges: list[str] = []
        self._year = ""
        self._genres: list[str] = []

    def _reset_card(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._release_text = ""
        self._quality_badges = []
        self._year = ""
        self._genres = []
        self._in_movie_title_div = False
        self._movie_title_tag = ""
        self._movie_title_div_depth = 0

    def _emit_card(self) -> None:
        if not self._current_title or not self._current_url:
            return

        year, genres = _parse_release_text(self._release_text)
        if not self._year:
            self._year = year
        if not self._genres:
            self._genres = genres

        is_series = _detect_series(self._genres)

        self.results.append(
            {
                "title": _clean_title(self._current_title),
                "url": self._current_url,
                "genres": list(self._genres),
                "year": self._year,
                "quality": self._quality_badges[0] if self._quality_badges else "",
                "is_series": is_series,
            }
        )

    def handle_starttag(  # noqa: C901
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if tag == "div":
            if self._in_card:
                self._card_div_depth += 1
                if "movie-title" in classes:
                    self._in_movie_title_div = True
                    self._movie_title_tag = "div"
                    self._movie_title_div_depth = 0
                elif self._in_movie_title_div:
                    self._movie_title_div_depth += 1
                if "movie-release" in classes:
                    self._in_release_div = True
                    self._release_text = ""
            elif "movie-preview" in classes and "res_item" in classes:
                self._in_card = True
                self._card_div_depth = 0
                self._reset_card()
            return

        if not self._in_card:
            return

        if tag == "a" and self._in_movie_title_div:
            href = attr_dict.get("href", "") or ""
            title_attr = attr_dict.get("title", "") or ""
            if href:
                self._current_url = urljoin(self._base_url, href)
            self._in_title_a = True
            self._current_title = title_attr or ""

        if tag == "span":
            if self._in_card and "movie-title" in classes:
                self._in_movie_title_div = True
                self._movie_title_tag = "span"
                self._movie_title_div_depth = 0
            for cls in classes:
                if cls.startswith("icon-"):
                    badge = cls.replace("icon-", "").upper()
                    if badge:
                        self._quality_badges.append(badge)

    def handle_data(self, data: str) -> None:
        if self._in_title_a and not self._current_title:
            self._current_title += data

        if self._in_release_div:
            self._release_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_a:
            self._in_title_a = False
            self._current_title = self._current_title.strip()

        if (
            tag == "span"
            and self._in_movie_title_div
            and self._movie_title_tag == "span"
        ):
            self._in_movie_title_div = False

        if tag == "div":
            if self._in_release_div:
                self._in_release_div = False

            if self._in_movie_title_div and self._movie_title_tag == "div":
                if self._movie_title_div_depth > 0:
                    self._movie_title_div_depth -= 1
                else:
                    self._in_movie_title_div = False

            if self._in_card:
                if self._card_div_depth > 0:
                    self._card_div_depth -= 1
                else:
                    self._in_card = False
                    self._emit_card()


class _DetailPageParser(HTMLParser):
    """Parse streamkiste detail page metadata.

    Stream links are not on the page itself; they come from the embedded
    devideosrc player (see ``scavengarr.infrastructure.plugins.devideosrc``).

    Metadata:
    - Title from h1
    - Year from .release text "(2026)"
    - Genres from .categories a links
    - Description from .info-right p
    - IMDb rating from .average span
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Metadata
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""

        # Title tracking (h1)
        self._in_h1 = False
        self._h1_text = ""

        # Release text
        self._in_release = False
        self._release_tag = ""
        self._release_text = ""

        # Categories (.categories div with a links)
        self._in_categories = False
        self._categories_div_depth = 0
        self._in_category_a = False
        self._category_text = ""

        # Description (.info-right p)
        self._in_info_right = False
        self._info_right_div_depth = 0
        self._in_desc_p = False
        self._desc_text = ""

        # IMDb rating (.average)
        self._in_average = False
        self._average_div_depth = 0
        self._in_average_span = False
        self._average_text = ""

    @property
    def is_series(self) -> bool:
        return _detect_series(self.genres)

    def handle_starttag(  # noqa: C901
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # h1
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # Release: <div class="release"> or <span class="release">
        if tag in ("div", "span") and "release" in classes:
            self._in_release = True
            self._release_tag = tag
            self._release_text = ""

        if tag == "div":
            # Categories area
            if self._in_categories:
                self._categories_div_depth += 1
            elif "categories" in classes:
                self._in_categories = True
                self._categories_div_depth = 0

            # Info-right area
            if self._in_info_right:
                self._info_right_div_depth += 1
            elif "info-right" in classes:
                self._in_info_right = True
                self._info_right_div_depth = 0

            # Average rating area
            if self._in_average:
                self._average_div_depth += 1
            elif "average" in classes:
                self._in_average = True
                self._average_div_depth = 0

        # Category link inside .categories
        if tag == "a" and self._in_categories:
            self._in_category_a = True
            self._category_text = ""

        # Description paragraph inside .info-right
        if tag == "p" and self._in_info_right:
            self._in_desc_p = True
            self._desc_text = ""

        # Rating span inside .average
        if tag == "span" and self._in_average:
            self._in_average_span = True
            self._average_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_release:
            self._release_text += data
        if self._in_category_a:
            self._category_text += data
        if self._in_desc_p:
            self._desc_text += data
        if self._in_average_span:
            self._average_text += data

    def _end_div(self) -> None:
        if self._in_categories:
            if self._categories_div_depth > 0:
                self._categories_div_depth -= 1
            else:
                self._in_categories = False

        if self._in_info_right:
            if self._info_right_div_depth > 0:
                self._info_right_div_depth -= 1
            else:
                self._in_info_right = False

        if self._in_average:
            if self._average_div_depth > 0:
                self._average_div_depth -= 1
            else:
                self._in_average = False

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = _clean_title(self._h1_text)

        if tag == self._release_tag and self._in_release:
            self._in_release = False
            text = self._release_text.strip()
            m = re.search(r"\(?\b((?:19|20)\d{2})\b\)?", text)
            if m:
                self.year = m.group(1)

        if tag == "a" and self._in_category_a:
            self._in_category_a = False
            text = self._category_text.strip()
            if text:
                self.genres.append(text)

        if tag == "p" and self._in_desc_p:
            self._in_desc_p = False
            self.description = self._desc_text.strip()

        if tag == "span" and self._in_average_span:
            self._in_average_span = False
            m = re.search(r"(\d+\.?\d*)", self._average_text.strip())
            if m:
                self.imdb_rating = m.group(1)

        if tag == "div":
            self._end_div()


class StreamkistePlugin(HttpxPluginBase):
    """Python plugin for streamkiste.taxi using httpx."""

    name = "streamkiste"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
        page: int = 1,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch a search results page.

        DLE CMS search::
            Page 1: GET /index.php?do=search&subaction=search&story={query}
            Page 2+: POST /index.php?do=search with form data
        """
        if page == 1:
            params: dict[str, str] = {
                "do": "search",
                "subaction": "search",
                "story": query,
            }
            html = await self._fetch_text(
                f"{self.base_url}/index.php", params=params, context="search"
            )
        else:
            form_data: dict[str, str] = {
                "do": "search",
                "subaction": "search",
                "search_start": str(page),
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
            "streamkiste_search_page",
            query=query,
            page=page,
            results=len(parser.results),
        )
        return parser.results

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch search results with pagination up to self.effective_max_results."""
        all_results: list[dict[str, str | list[str] | bool]] = []

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
        result: dict[str, str | list[str] | bool],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Scrape a detail page for metadata, then load its devideosrc links."""
        detail_url = str(result["url"])
        html = await self._fetch_text(detail_url, context="detail")
        if html is None:
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(html)

        player = devideosrc.find_player(html)
        if player is None:
            self._log.debug("streamkiste_no_player", url=detail_url)
            return None

        client = await self._ensure_client()
        found = await devideosrc.fetch_links(
            client, player, **self._request_kwargs(client)
        )
        links = found.links
        # the site embeds the series player for movies too: trust the answer
        is_series = found.kind == "tv" or parser.is_series

        if is_series and season is not None and links:
            links = devideosrc.filter_episodes(links, season, episode)

        if not links:
            self._log.debug("streamkiste_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        year = parser.year or str(result.get("year", ""))
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
            "imdb_id": player.imdb_id,
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
        """Search streamkiste.taxi and return results with stream links."""
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

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        sem = self._new_semaphore()

        async def _bounded(
            r: dict[str, str | list[str] | bool],
        ) -> SearchResult | None:
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


plugin = StreamkistePlugin()
