"""streamkiste Python plugin for Scavengarr.

Scrapes streamkiste.onl (German streaming site, Next.js front end since
2026-11) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /suche?q={query}; one result page, the site caps the hit list itself
- Detail page scraping for metadata; stream/hoster links come from the
  embedded devideosrc player (``scavengarr.infrastructure.plugins.devideosrc``),
  whose URL already carries the IMDb id: ``/movie/tt…`` for a film and
  ``/serial/tt…`` for a series
- Series detection from the player answer and ``og:type=video.tv_show``
- Season/episode filtering of series links
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Domain: streamkiste.onl. The former mirrors (.bid, .taxi, .tv, .sx, .al, .city)
are dead or redirect to .onl, and the legacy DLE search form is gone with them.
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
_DOMAINS = ["streamkiste.onl"]
_MAX_PAGES = 1  # the 2026-11 front end has one page and ignores ?page=

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _clean_title(title: str) -> str:
    """Strip common suffixes and trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


class _SearchResultParser:
    """Parse a streamkiste search result page (selectolax).

    The 2026-11 Next.js front end renders one ``div.grid a.cv`` anchor per hit
    (see ``_add_cv_card``). The 2026-10 DLE theme's ``movie-preview res_item``
    cards are still parsed although nothing serves them since 2026-11 (no host
    in ``_domains``, no archived capture); dead code until the follow-up that
    deletes it::

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
        for card in tree.css("div.grid a.cv"):
            self._add_cv_card(card)

    def _add_cv_card(self, card: LexborNode) -> None:
        """Add a 2026-11 ``div.grid a.cv`` result card.

        The Next.js front end renders one anchor per hit::

            <a class="cv" href="/movie/27205-inception-stream-kostenlos"
               title="Inception">
              <div class="cv-art"><img ...><span class="qual"><b>HD</b>…
              <div class="cv-t">Inception</div>
              <div class="cv-m">2010</div>
            </a>

        The card carries no genre; ``span.tag-serie`` and ``div.cv-m == "Serie"``
        mark a series, and the detail page's player names the kind as well.
        """
        href = (card.attributes.get("href") or "").strip()
        if not href:
            return

        title_node = card.css_first("div.cv-t")
        title = title_node.text().strip() if title_node else ""
        if not title:
            title = (card.attributes.get("title") or "").strip()
        if not title:
            image = card.css_first("img[alt]")
            title = (image.attributes.get("alt") or "").strip() if image else ""
        if not title:
            return

        year = ""
        year_node = card.css_first("div.cv-m")
        if year_node:
            match = re.search(r"\b(?:19|20)\d{2}\b", year_node.text())
            year = match.group(0) if match else ""

        badges = [
            badge.text().strip().upper()
            for badge in card.css("span.qual b")
            if badge.text().strip()
        ]

        url = urljoin(self._base_url, href)
        if any(result["url"] == url for result in self.results):
            return
        self.results.append(
            {
                "title": _clean_title(title),
                "url": url,
                "genres": [],
                "year": year,
                "quality": badges[0] if badges else "",
                "is_series": card.css_first("span.tag-serie") is not None,
            }
        )


class _DetailPageParser:
    """Parse streamkiste detail page metadata (selectolax).

    Stream links are not on the page itself; they come from the embedded
    devideosrc player (see ``scavengarr.infrastructure.plugins.devideosrc``).

    Metadata:
    - Title from the last h1
    - Year from ``span.sk-det-jahr`` (2026-11) or the first ``.release`` text
      "(2026)" with one
    - Genres from ``div.sk-det-genres a`` or ``.categories a`` links
    - Description from the last ``div.sk-det-trama p`` (2026-11, the plot) or
      ``div.info-right p``
    - IMDb rating from ``span.sk-stern`` (2026-11, "8,8") or the first
      ``.average`` with a number: the badge of the IMDb link
      (``<a href="…imdb.com/title/…"><span class="average">``); the old theme
      nested a span in ``div.average``
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""
        self._og_series = False

    @property
    def is_series(self) -> bool:
        return self._og_series

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_og_meta(tree)
        for h1 in tree.css("h1"):
            self.title = _clean_title(h1.text())
        self._read_year(tree)
        self._read_genres(tree)
        self._read_description(tree)
        self._read_rating(tree)

    def _read_year(self, tree: LexborHTMLParser) -> None:
        """The page's own year wins over the document title's.

        A series has no year in its title, only in ``span.sk-det-jahr``; the
        2026-10 theme carries it in the first ``.release`` instead (the related
        films below have their own).
        """
        jahr = tree.css_first("span.sk-det-jahr")
        if jahr is not None:
            m = re.search(r"((?:19|20)\d{2})", jahr.text())
            if m:
                self.year = m.group(1)
                return
        for release in tree.css("div.release, span.release"):
            m = re.search(r"\(?\b((?:19|20)\d{2})\b\)?", release.text())
            if m:
                self.year = m.group(1)
                return

    def _read_genres(self, tree: LexborHTMLParser) -> None:
        for link in tree.css("div.categories a, div.sk-det-genres a"):
            text = link.text().strip()
            if text and text not in self.genres:
                self.genres.append(text)

    def _read_description(self, tree: LexborHTMLParser) -> None:
        # 2026-11: the plot is in .sk-det-trama, the OG blurb is truncated SEO
        paragraphs = tree.css("div.info-right p, div.sk-det-trama p")
        if paragraphs:
            text = paragraphs[-1].text().strip()
            if text:  # an empty plot keeps the og:description blurb
                self.description = text

    def _read_rating(self, tree: LexborHTMLParser) -> None:
        for node in tree.css(".average, span.sk-stern"):
            m = re.search(r"(\d+[.,]?\d*)", node.text())
            if m:
                self.imdb_rating = m.group(1).replace(",", ".")
                return

    def _read_og_meta(self, tree: LexborHTMLParser) -> None:
        """Read the 2026-11 Next.js page's Open Graph metadata.

        ``og:type`` names the kind (``video.movie`` / ``video.tv_show``) and
        ``og:description`` carries the site's SEO blurb; the page body adds the
        year (``span.sk-det-jahr``), the genres (``div.sk-det-genres``), the
        rating (``span.sk-stern``) and the plot (``div.sk-det-trama p``), which
        are read below and win over the blurb.
        """
        og_type = tree.css_first("meta[property='og:type']")
        if og_type is not None:
            self._og_series = (
                og_type.attributes.get("content") or ""
            ) == "video.tv_show"

        title_tag = tree.css_first("title")
        if title_tag is not None:
            match = re.search(r"\(((?:19|20)\d{2})\)", title_tag.text())
            if match and not self.year:
                self.year = match.group(1)

        og_description = tree.css_first("meta[property='og:description']")
        if og_description is not None and not self.description:
            text = " ".join((og_description.attributes.get("content") or "").split())
            if text:
                self.description = text


class StreamkistePlugin(HttpxPluginBase):
    """Python plugin for streamkiste.onl using httpx."""

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

        2026-11 front end: one ``GET /suche?q={query}`` page. The site caps
        the hit list itself and ignores ``?page=``, so only page 1 exists.
        """
        if page != 1:
            return []

        html = await self._fetch_search_page("/suche", params={"q": query})
        if html is None:
            return []

        parser = await parse_page(_SearchResultParser(self.base_url), html)

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

        parser = await parse_page(_DetailPageParser(self.base_url), html)

        player = devideosrc.find_player(html)
        if player is None:
            self._log.debug("streamkiste_no_player", url=detail_url)
            return None

        client = await self._ensure_client()
        found = await devideosrc.fetch_links(
            client, player, **self._request_kwargs(client)
        )
        links = found.links
        # trust the player's kind, but a series genre also makes it one
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
        """Search streamkiste.onl and return results with stream links."""
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
