"""streamcloud Python plugin for Scavengarr.

Scrapes the German streaming site of the streamcloud network (currently
streamcloud.date) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /suche?q={query} for keyword search (2026-11 layout; the old DLE
  ``/?do=search&subaction=search&story={query}`` URL is kept as a fallback)
- The 2026-11 search is a single page of up to ~60 hits and ignores ``?page=``;
  the legacy DLE POST pagination (search_start/result_from) remains for old
  mirrors
- Detail page scraping for metadata; stream/hoster links come from the
  embedded devideosrc player (movie or series, see
  ``scavengarr.infrastructure.plugins.devideosrc``)
- Series detection from the player kind, ``og:type`` and the "Serien" genre
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Domains: streamcloud.date is the current host (2026-11);
streamcloud.download and streamcloud.plus redirect to it. The site moved
from the DLE ``div.item.cf`` cards to ``div.grid > a.cv`` cards; the parser
accepts both. No authentication required.
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
from scavengarr.infrastructure.plugins.constants import search_external_ids
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
_DOMAINS = ["streamcloud.date", "streamcloud.download", "streamcloud.plus"]
_RESULTS_PER_PAGE = 12
_MAX_PAGES = 84  # 12 results/page → 84 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A title search scrapes the closest hits only: a generic title can match
# dozens of site titles ("Dark" matches 60), and each detail costs a player.
_TITLE_SCRAPE_HITS = 5

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
    """Parse streamcloud DLE search result page (selectolax).

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

    The 2026-11 layout uses one anchor per result in a ``div.grid``::

        <a class="cv" href="/filme/{id}-{slug}-stream-deutsch" title="Title">
          <div class="cv-art">...</div>
          <div class="cv-t">Title</div>
          <div class="cv-m">1995 · ★ 8,0</div>
        </a>
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self._base_url = base_url
        # "cv" (2026-11) has no pagination; "legacy" uses the DLE POST
        self.layout = "legacy"

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        cards = tree.css("div.grid a.cv")
        if cards:
            self.layout = "cv"
            for card in cards:
                self._add_cv_card(card)
            return
        for card in tree.css("div.item.cf"):
            self._add_card(card)

    def _add_cv_card(self, card: LexborNode) -> None:
        """Add a 2026-11 ``a.cv`` result card."""
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

        url = urljoin(self._base_url, href)
        if any(result["url"] == url for result in self.results):
            return
        self.results.append(
            {
                "title": _clean_title(title),
                "url": url,
                "year": year,
            }
        )

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

    A label's value is the first ``<div>`` or ``<span>`` after it. The new
    layout is read first; the legacy readers only fill fields that are still
    empty, so the first year link and IMDb link win. The description is the
    new layout's ``p.dsyn``; otherwise the first paragraph with more than
    20 characters (legacy layout).
    """

    # Labels whose value is read, and the field it fills
    _FIELDS = {"Genres:": "genres", "Spielzeit:": "runtime"}

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""
        self.imdb_id = ""
        self.runtime = ""
        self._is_series = False

    @property
    def is_series(self) -> bool:
        return self._is_series or _detect_series(self.genres)

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_new_layout(tree)
        self._read_og_type(tree)
        self._read_fields(tree)
        # Year link: <a href="/xfsearch/2008">2008</a>
        for link in tree.css("a[href*='/xfsearch/']"):
            text = link.text().strip()
            href = link.attributes.get("href") or ""
            if (
                not self.year
                and re.search(r"/xfsearch/\d{4}$", href)
                and re.match(r"\d{4}$", text)
            ):
                self.year = text
        for link in tree.css("a[href*='imdb.com/title/']"):
            imdb_id = re.search(r"(tt\d+)", link.attributes.get("href") or "")
            if imdb_id and not self.imdb_id:
                self.imdb_id = imdb_id.group(1)
            rating = re.search(r"(\d+[.,]?\d*)/10", link.text().strip())
            if rating and not self.imdb_rating:
                self.imdb_rating = rating.group(1).replace(",", ".")
        if not self.description:
            for paragraph in tree.css("p"):
                text = paragraph.text().strip()
                if len(text) > 20:
                    self.description = text
                    break

    def _read_new_layout(self, tree: LexborHTMLParser) -> None:
        """Read the 2026-11 detail page: h1, ``div.sc-riga`` rows, ``p.dsyn``."""
        title = tree.css_first("h1.sc-h1")
        if title is None and tree.css_first("div.sc-riga") is not None:
            # A new-layout page without the class; the legacy page's first h1
            # is the site logo ("StreamCloud"), never the title.
            title = tree.css_first("h1")
        if title is not None:
            self.title = title.text().strip()
        for row in tree.css("div.sc-riga"):
            self._read_new_row(row)
        synopsis = tree.css_first("div.sc-info p.dsyn") or tree.css_first("p.dsyn")
        if synopsis is not None:
            text = " ".join(synopsis.text().split())
            if text:
                self.description = text
        imdb = tree.css_first("a.sc-imdb[href*='imdb.com/title/']")
        if imdb is not None:
            self._read_new_imdb(imdb)

    def _read_og_type(self, tree: LexborHTMLParser) -> None:
        """The new pages mark a series with ``og:type=video.tv_show``."""
        meta = tree.css_first("meta[property='og:type']")
        if meta is None:
            return
        content = (meta.attributes.get("content") or "").lower()
        if "video.tv" in content or "video.episode" in content:
            self._is_series = True

    def _read_new_row(self, row: LexborNode) -> None:
        """Read one ``div.sc-riga`` label/value row."""
        label_node = row.css_first("strong")
        value_node = row.css_first("div")
        if label_node is None or value_node is None:
            return
        label = label_node.text().strip().removesuffix(":").strip().lower()
        if label == "genres":
            self._read_new_genres(value_node)
        elif label in ("veröffentlicht", "veroeffentlicht", "jahr"):
            match = re.search(r"\b(?:19|20)\d{2}\b", value_node.text())
            if match:
                self.year = match.group(0)
        elif label == "spielzeit":
            match = re.search(r"(\d+)\s*min", value_node.text(), re.I)
            if match:
                self.runtime = f"{match.group(1)} min"

    def _read_new_genres(self, value_node: LexborNode) -> None:
        genres: list[str] = []
        for link in value_node.css("a[href*='/genre/']"):
            genre = link.text().strip()
            if genre and genre not in genres:
                genres.append(genre)
        if not genres:
            raw = " ".join(value_node.text().split())
            genres = [genre.strip() for genre in raw.split("/") if genre.strip()]
        if genres:
            self.genres = genres

    def _read_new_imdb(self, imdb: LexborNode) -> None:
        match = re.search(r"(tt\d+)", imdb.attributes.get("href") or "")
        if match:
            self.imdb_id = match.group(1)
        rating = re.search(r"(\d+[.,]?\d*)\s*/\s*10", imdb.text())
        if rating:
            self.imdb_rating = rating.group(1).replace(",", ".")

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
            if field == "genres" and not self.genres:
                self.genres = [g.strip() for g in raw.split("/") if g.strip()]
            elif field == "runtime" and not self.runtime:
                self.runtime = raw
            label = ""


class StreamcloudPlugin(HttpxPluginBase):
    """Python plugin for the streamcloud network using httpx."""

    name = "streamcloud"
    provides = "stream"
    _domains = _DOMAINS
    # One database behind hdfilme, streamcloud and streamkiste (same news ids)
    mirror_group = "hdfilme"

    async def _search_page(
        self,
        query: str,
        page: int = 1,
    ) -> tuple[list[dict[str, str]], str]:
        """Fetch one search page and its layout (``"cv"`` or ``"legacy"``).

        The 2026-11 site answers ``GET /suche?q={query}`` with up to ~60 cards
        in one page; it ignores ``?page=``. The legacy DLE search
        ``/?do=search&subaction=search&story={query}`` and its POST pagination
        (``search_start``/``result_from``) are the fallback for old mirrors.
        The layout comes back with the results, so concurrent searches do not
        share mutable pagination state on the plugin singleton.
        """
        if page == 1:
            # The first page raises on a failure of the site, so the plugin
            # record counts it; a later page that fails ends the pagination
            html = await self._fetch_search_page("/suche", params={"q": query})
            if html is None:
                # legacy DLE search, still answered by old mirrors
                html = await self._fetch_search_page(
                    "/",
                    params={
                        "do": "search",
                        "subaction": "search",
                        "story": query,
                    },
                )
        else:
            html = await self._fetch_search_page(
                "/suche", params={"q": query, "page": str(page)}
            )
            if html is None:
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
            return [], "legacy"

        parser = await parse_page(_SearchResultParser(self.base_url), html)

        self._log.info(
            "streamcloud_search_page",
            query=query,
            page=page,
            layout=parser.layout,
            results=len(parser.results),
        )
        return parser.results, parser.layout

    async def _search_all_pages(
        self,
        query: str,
    ) -> list[dict[str, str]]:
        """Fetch search results with pagination up to _max_results."""
        all_results: list[dict[str, str]] = []
        seen: set[str] = set()

        for page_num in range(1, _MAX_PAGES + 1):
            results, layout = await self._search_page(query, page_num)
            if not results:
                break
            # The new /suche layout may ignore ?page= and answer the first
            # page again: stop when a page adds no new URL.
            fresh = [result for result in results if result["url"] not in seen]
            if not fresh:
                break
            seen.update(result["url"] for result in fresh)
            all_results.extend(fresh)
            if len(all_results) >= self.effective_max_results:
                break
            if layout == "cv":
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

        title = parser.title or _clean_title(result.get("title", ""))
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

    async def _direct_lookup(
        self,
        query: str,
        category: int | None,
        season: int | None,
        episode: int | None,
    ) -> list[SearchResult]:
        """Open the title's page by TMDB id when the site search misses it.

        A short or localized title (``Es``/``It``) can be absent from the
        site's search results although its page exists; the page accepts the
        TMDB id without a slug. An empty result leaves the caller to run the
        normal text search.
        """
        external = search_external_ids.get(None)
        if external is None or external.tmdb_id is None:
            return []
        kind = external.content_type
        if kind is None:
            kind = "series" if category == 5000 or season is not None else "movie"
        path = (
            f"/serie/{external.tmdb_id}"
            if kind == "series"
            else f"/filme/{external.tmdb_id}"
        )
        hit = {
            "title": query,
            "url": f"{self.base_url}{path}",
            "year": "",
        }
        found = await self._scrape_detail(hit, season=season, episode=episode)
        results = [found] if found is not None else []
        if category is not None:
            results = filter_by_category(results, category)
        self._log.info(
            "streamcloud_direct_lookup",
            tmdb_id=external.tmdb_id,
            kind=kind,
            result_count=len(results),
        )
        return results

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search the streamcloud network and return results with stream links."""
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

        direct = await self._direct_lookup(query, category, season, episode)
        if direct:
            return direct

        # Each hit costs a detail page and its player: scrape real matches only
        all_items = relevant_hits(
            await self._search_all_pages(query),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )[:_TITLE_SCRAPE_HITS]
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
