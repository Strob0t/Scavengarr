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

Multi-domain support: streamcloud.download (primary), streamcloud.plus
(fallback; it, .press, .uno and .forum redirect to the primary, so a new
primary is followed). streamcloud.my is a gambling site since 2026.
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
from scavengarr.infrastructure.plugins.dom import parse_page
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
_DOMAINS = ["streamcloud.download", "streamcloud.plus"]
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


def _hrefs(links: list[LexborNode]) -> list[str]:
    """The non-empty ``href`` values of *links*."""
    return [href for link in links if (href := link.attributes.get("href") or "")]


class _SearchResultParser:
    """Parse streamcloud.plus DLE search result page (selectolax).

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

    The (last) thumb link names the detail page, the first title link is
    the fallback; the (last) title link's text names the result, the
    thumb's ``title`` is the fallback.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        for card in LexborHTMLParser(html).css("div.item.cf"):
            self._add_card(card)

    def _add_card(self, card: LexborNode) -> None:
        title_links = card.css("div.f_title a")
        thumb_hrefs = _hrefs(card.css("div.thumb a"))
        title_hrefs = _hrefs(title_links)
        href = thumb_hrefs[-1] if thumb_hrefs else ""
        if not href and title_hrefs:
            href = title_hrefs[0]
        title = title_links[-1].text().strip() if title_links else ""
        thumbs = card.css("div.thumb")
        if not title and thumbs:
            title = thumbs[-1].attributes.get("title") or ""
        if not title or not href:
            return
        years = card.css("div.f_year")
        self.results.append(
            {
                "title": _clean_title(title),
                "url": urljoin(self._base_url, href),
                "year": years[-1].text().strip() if years else "",
            }
        )


class _DetailPageParser:
    """Parse streamcloud detail page metadata (selectolax).

    Stream links are not on the page itself; they come from the embedded
    devideosrc player (see ``scavengarr.infrastructure.plugins.devideosrc``).

    Metadata fields (value in a ``<div>`` or ``<span>`` after the label)::

        <strong>Genres: </strong> <div>Serien / Krimi / Drama</div>
        <strong>Veröffentlicht: </strong> <div><a href="/xfsearch/2008">2008</a></div>
        <strong>Spielzeit: </strong> <div>50 min</div>
        IMDb link: <a href="https://www.imdb.com/title/ttXXXXX/">6.1/10</a>

    A label's value is the first ``<div>`` or ``<span>`` after it; the last
    value of a field, year link and IMDb link wins. The description is the
    first paragraph with more than 20 characters.
    """

    # Labels whose value is read, and the field it fills
    _FIELDS = {"Genres:": "genres", "Spielzeit:": "runtime"}

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""
        self.imdb_id = ""
        self.runtime = ""

    @property
    def is_series(self) -> bool:
        return _detect_series(self.genres)

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_fields(tree)
        # Year link: <a href="/xfsearch/2008">2008</a>
        for link in tree.css("a[href*='/xfsearch/']"):
            text = link.text().strip()
            href = link.attributes.get("href") or ""
            if re.search(r"/xfsearch/\d{4}$", href) and re.match(r"\d{4}$", text):
                self.year = text
        for link in tree.css("a[href*='imdb.com/title/']"):
            imdb_id = re.search(r"(tt\d+)", link.attributes.get("href") or "")
            if imdb_id:
                self.imdb_id = imdb_id.group(1)
            rating = re.search(r"(\d+\.?\d*)/10", link.text().strip())
            if rating:
                self.imdb_rating = rating.group(1)
        for paragraph in tree.css("p"):
            text = paragraph.text().strip()
            if len(text) > 20:
                self.description = text
                break

    def _read_fields(self, tree: LexborHTMLParser) -> None:
        """Values of the "Genres:" and "Spielzeit:" labels."""
        label = ""
        for node in tree.css("strong, span, div"):
            if node.tag == "strong":
                label = node.text().strip()
                continue
            field = self._FIELDS.get(label)
            if not field:
                continue
            raw = " ".join(node.text().split())
            if field == "genres":
                self.genres = [g.strip() for g in raw.split("/") if g.strip()]
            else:
                self.runtime = raw
            label = ""


class StreamcloudPlugin(HttpxPluginBase):
    """Python plugin for streamcloud.plus using httpx."""

    name = "streamcloud"
    provides = "stream"
    _domains = _DOMAINS
    # One database behind hdfilme, streamcloud and streamkiste (same news ids)
    mirror_group = "hdfilme"

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

        parser = await parse_page(_SearchResultParser(self.base_url), html)

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

        parser = await parse_page(_DetailPageParser(self.base_url), html)

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
            links = filter_episodes(links, season, episode)
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
        all_items = relevant_hits(
            await self._search_all_pages(query),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
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
