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
from scavengarr.infrastructure.plugins.dom import ancestors, classes
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


class _SearchResultParser:
    """Parse streamkiste.taxi DLE search result page (selectolax).

    Each result is a card with structure like::

        <div class="movie-preview res_item">
          <span class="movie-title">   (or <div class="movie-title">)
            <a href="/film/12345-title.html" title="Title">Title</a>
          </span>
          <span class="movie-release">2025 - Action Komödie kinofilme</span>
          <div class="ico-bar">
            <span class="icon-hd"></span>
          </div>
        </div>

    A card inside another card belongs to the outer one. The last link of
    a card's ``movie-title`` names the result, the last ``movie-release``
    (a span in the current theme, a div before) gives its year and genres,
    the first ``icon-*`` span its quality.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for card in tree.css("div.movie-preview.res_item"):
            if not any(_is_card(parent) for parent in ancestors(card)):
                self._add_card(card)

    def _add_card(self, card: LexborNode) -> None:
        title = url = ""
        for link in card.css("div.movie-title a, span.movie-title a"):
            href = link.attributes.get("href") or ""
            if href:
                url = urljoin(self._base_url, href)
            title = (link.attributes.get("title") or link.text()).strip()
        if not title or not url:
            return

        releases = card.css(".movie-release")
        year, genres = _parse_release_text(releases[-1].text() if releases else "")
        badges = [
            badge
            for span in card.css("span")
            for cls in classes(span)
            if cls.startswith("icon-") and (badge := cls.replace("icon-", "").upper())
        ]

        self.results.append(
            {
                "title": _clean_title(title),
                "url": url,
                "genres": genres,
                "year": year,
                "quality": badges[0] if badges else "",
                "is_series": _detect_series(genres),
            }
        )


def _is_card(node: LexborNode) -> bool:
    """Whether *node* is a search result card."""
    names = classes(node)
    return node.tag == "div" and "movie-preview" in names and "res_item" in names


class _DetailPageParser:
    """Parse streamkiste detail page metadata (selectolax).

    Stream links are not on the page itself; they come from the embedded
    devideosrc player (see ``scavengarr.infrastructure.plugins.devideosrc``).

    Metadata:
    - Title from the last h1
    - Year from the first .release text "(2026)" with one
    - Genres from .categories a links
    - Description from the last .info-right p
    - IMDb rating from the first .average with a number: the badge of the
      IMDb link (``<a href="…imdb.com/title/…"><span class="average">``);
      the old theme nested a span in ``div.average``
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""

    @property
    def is_series(self) -> bool:
        return _detect_series(self.genres)

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for h1 in tree.css("h1"):
            self.title = _clean_title(h1.text())
        # The film's .release comes first; related films below have their own
        for release in tree.css("div.release, span.release"):
            m = re.search(r"\(?\b((?:19|20)\d{2})\b\)?", release.text())
            if m:
                self.year = m.group(1)
                break
        for link in tree.css("div.categories a"):
            text = link.text().strip()
            if text:
                self.genres.append(text)
        paragraphs = tree.css("div.info-right p")
        if paragraphs:
            self.description = paragraphs[-1].text().strip()
        for node in tree.css(".average"):
            m = re.search(r"(\d+\.?\d*)", node.text())
            if m:
                self.imdb_rating = m.group(1)
                break


class StreamkistePlugin(HttpxPluginBase):
    """Python plugin for streamkiste.taxi using httpx."""

    name = "streamkiste"
    provides = "stream"
    _domains = _DOMAINS
    # One database behind hdfilme, streamcloud and streamkiste (same news ids)
    mirror_group = "hdfilme"

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
            links = filter_episodes(links, season, episode)

        if not links:
            self._log.debug("streamkiste_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        listed = result.get("genres")
        genres = parser.genres or (list(listed) if isinstance(listed, list) else [])
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

        # Each hit costs a detail page and its player: scrape real matches only
        all_items = relevant_hits(
            await self._search_all_pages(query),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
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
