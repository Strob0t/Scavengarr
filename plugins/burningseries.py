"""bs.to (Burning Series) Python plugin for Scavengarr.

Scrapes Burning Series (German TV series streaming aggregator) with:
- httpx for all requests (server-rendered pages, no JS challenges)
- Series listing from /andere-serien (all series grouped by genre)
- Series pages at /serie/{slug}, season pages at /serie/{slug}/{season}/de
  (German episode list; the site's default language when there is none)
- TV series only: Anime, Comedy, Drama, Documentary, etc.

The hoster links sit behind reCAPTCHA v2 (``/ajax/embed.php``), so the
results link Burning Series pages: series, season or episode page, which
JDownloader's BsTo crawler resolves (captcha solved in JDownloader). Hence
``provides = "download"``: no stream for Stremio.

Multi-domain support with automatic fallback.
No authentication required for browsing.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
# Genuine domains as listed by JDownloader's BsTo (burningseries.domains).
# bs.to and burningseries.co are gone; burning-series.io/.net/.fun and
# bs-to.fun are clones that swap the player for their own redirect.
_DOMAINS = ["burningseries.ac", "bs.cine.to", "burningseries.sx"]
_MAX_SERIES_DETAIL = 50  # Max series to fetch detail pages for

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Genre -> Torznab category mapping.
# bs.to only has TV series, so all categories are in the 5xxx range.
_GENRE_CATEGORY: dict[str, int] = {
    "anime": 5070,
    "anime-china": 5070,
    "anime-ecchi": 5070,
    "anime-horror": 5070,
    "anime-isekai": 5070,
    "anime-mecha": 5070,
    "anime-musik": 5070,
    "anime-romance": 5070,
    "anime-slice of life": 5070,
    "anime-sport": 5070,
    "anime-super-power": 5070,
    "anime-supernatural": 5070,
    "dokumentation": 5080,
    "dokusoap": 5080,
    "sport": 5060,
}

# Episode page in the episode table (hoster links add a /<Hoster> segment)
_EPISODE_HREF_RE = re.compile(r"^serie/[^/]+/\d+/(\d+)-[^/]+/[a-z]+$")


class _SeriesListParser:
    """Parse /andere-serien for series names, URL slugs and genres (selectolax).

    HTML structure::

        <div class="genre">
          <span><strong>Abenteuer</strong></span>
          <ul>
            <li><a href="serie/Name-Slug" title="Title">Title</a></li>
            ...
          </ul>
        </div>

    A ``serie/`` link takes the genre of the last ``strong`` before it.
    """

    def __init__(self) -> None:
        self.series: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        genre = ""
        tree = LexborHTMLParser(html)
        for node in tree.css("div.genre strong, div.genre a[href*='serie/']"):
            if node.tag == "strong":
                genre = node.text().strip()
                continue
            title = node.text().strip()
            href = (node.attributes.get("href") or "").strip()
            if title and href:
                # Extract slug from href like "serie/Breaking-Bad"
                slug = href.replace("serie/", "").strip("/")
                self.series.append({"title": title, "slug": slug, "genre": genre})


class _SeriesDetailParser:
    """Parse a series detail page at /serie/{slug} (selectolax).

    Extracts title, description, genres, year range, season count,
    and episode info from the series page HTML.

    HTML structure::

        <section class="serie">
          <div id="sp_left">
            <h2>Breaking Bad <small>Staffel 1</small></h2>
            <p>Description...</p>
            <div class="infos">
              <div>
                <span>Genres</span>
                <p><span>Drama</span> <span>Krimi</span></p>
              </div>
              <div>
                <span>Produktionsjahre</span>
                <p><em>2008 - 2013</em></p>
              </div>
            </div>
          </div>
          <div id="seasons"><ul><li>...</li></ul></div>
          <table class="episodes"><tbody><tr>...</tr></tbody></table>
        </section>

    The first ``h2`` of ``sp_left`` names the series (without its
    ``small``), the first ``p`` after it outside ``infos`` describes it.
    An info label ``span`` names the next ``p`` of ``infos``.
    """

    def __init__(self) -> None:
        self.title = ""
        self.description = ""
        self.genres: list[str] = []
        self.year = ""
        self.season_count = 0
        self.episode_count = 0
        # Episode page per number; rows without hosters are marked "disabled"
        self.episode_links: dict[int, str] = {}

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_heading(tree)
        self._read_infos(tree)
        self.season_count = len(tree.css("div#seasons li"))
        self._read_episodes(tree)

    def _read_heading(self, tree: LexborHTMLParser) -> None:
        heading = False
        for node in tree.css("div#sp_left h2, div#sp_left p"):
            if not heading:
                if node.tag == "h2":
                    heading = True
                    self.title = _text_outside(node, "small").strip()
            elif node.tag == "p" and not any(
                parent.tag == "div" and "infos" in classes(parent)
                for parent in ancestors(node)
            ):
                self.description = node.text().strip()
                return

    def _read_infos(self, tree: LexborHTMLParser) -> None:
        label = ""
        for node in tree.css("div.infos span, div.infos p"):
            if node.tag == "p":
                self._read_info(label.strip(), node)
                label = ""  # a label names one value
            elif not any(parent.tag == "p" for parent in ancestors(node)):
                label = node.text()

    def _read_info(self, label: str, value: LexborNode) -> None:
        if label == "Genres":
            for span in value.css("span"):
                genre = span.text().strip().rstrip(",")
                if genre:
                    self.genres.append(genre)
        elif label == "Produktionsjahre":
            # Each text stripped: "2023 - <i>Unbekannt</i>" is "2023 -Unbekannt"
            for em in value.css("em"):
                self.year += em.text(strip=True)

    def _read_episodes(self, tree: LexborHTMLParser) -> None:
        rows = tree.css("table.episodes tr")
        self.episode_count = len(rows)
        for row in rows:
            if "disabled" in classes(row):
                continue
            for link in row.css("a"):
                match = _EPISODE_HREF_RE.match(link.attributes.get("href") or "")
                if match:
                    self.episode_links.setdefault(int(match.group(1)), match.group(0))


def _text_outside(node: LexborNode, tag: str) -> str:
    """The text of *node* without the text of its *tag* elements."""
    parts: list[str] = []
    for child in node.iter(include_text=True):
        if child.is_text_node:
            parts.append(child.text_content or "")
        elif child.tag != tag:
            parts.append(_text_outside(child, tag))
    return "".join(parts)


def _genre_to_category(genre: str) -> int:
    """Map a bs.to genre name to a Torznab category."""
    key = genre.lower().strip()
    return _GENRE_CATEGORY.get(key, 5000)


def _match_query(query: str, title: str) -> bool:
    """Check if all query words appear in the title (case-insensitive).

    Strips punctuation from both query and title before matching so that
    queries like ``"Naruto:"`` match titles like ``"Naruto Shippuuden"``.
    """
    clean = re.compile(r"[^\w\s]")
    query_words = clean.sub("", query.lower()).split()
    title_clean = clean.sub("", title.lower())
    return all(word in title_clean for word in query_words)


class BurningSeriesPlugin(HttpxPluginBase):
    """Python plugin for bs.to / Burning Series using httpx."""

    name = "burningseries"
    provides = "download"
    _domains = _DOMAINS
    # The series listing (/andere-serien) is a large page
    _timeout = 30.0

    def __init__(self) -> None:
        super().__init__()
        self._series_cache: list[dict[str, str]] | None = None

    async def _fetch_series_listing(self) -> list[dict[str, str]]:
        """Fetch and parse the full series listing, cached for plugin lifetime."""
        if self._series_cache is not None:
            return self._series_cache

        html = await self._fetch_text(
            f"{self.base_url}/andere-serien", context="listing"
        )
        if html is None:
            return []

        parser = await parse_page(_SeriesListParser(), html)

        # Deduplicate by slug (a series can appear in multiple genre sections)
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for entry in parser.series:
            slug = entry["slug"]
            if slug not in seen:
                seen.add(slug)
                unique.append(entry)

        self._series_cache = unique
        self._log.info(
            "burningseries_listing_loaded",
            total=len(parser.series),
            unique=len(unique),
        )
        return unique

    async def _fetch_detail(
        self, slug: str, season: int | None = None
    ) -> _SeriesDetailParser:
        """Fetch and parse the series page, or the German season page."""
        path = f"/serie/{slug}" if season is None else f"/serie/{slug}/{season}/de"
        html = await self._fetch_text(f"{self.base_url}{path}", context="detail")
        if html is None:
            return _SeriesDetailParser()

        parser = await parse_page(_SeriesDetailParser(), html)

        self._log.info(
            "burningseries_detail",
            slug=slug,
            title=parser.title,
            year=parser.year,
            seasons=parser.season_count,
            episodes=parser.episode_count,
        )
        return parser

    def _build_search_result(
        self,
        listing_entry: dict[str, str],
        detail: _SeriesDetailParser,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Build a SearchResult from listing entry + detail page data.

        Links the series page, the German season page, or the episode page
        from the season's episode table (None when the episode has no
        hosters there).
        """
        title = detail.title or listing_entry["title"]
        year = detail.year
        slug = listing_entry["slug"]
        genre = listing_entry.get("genre", "")
        source_url = f"{self.base_url}/serie/{slug}"

        if season is None:
            download_url = source_url
            display_title = f"{title} ({year})" if year else title
        elif episode is None:
            download_url = f"{source_url}/{season}/de"
            display_title = f"{title} S{season:02d}"
        else:
            href = detail.episode_links.get(episode)
            if href is None:
                return None
            download_url = urljoin(f"{self.base_url}/", href)
            display_title = f"{title} S{season:02d}E{episode:02d}"

        # Category from genre
        category = _genre_to_category(genre)
        # "2008 - 2013", "seit 2019", or no year at all
        year_match = re.search(r"\d{4}", year)

        # Build description
        desc_parts: list[str] = []
        if detail.genres:
            desc_parts.append(", ".join(detail.genres))
        if detail.season_count:
            desc_parts.append(f"{detail.season_count} Staffeln")
        if detail.episode_count:
            desc_parts.append(f"{detail.episode_count} Episoden (S{season or 1})")
        description = " | ".join(desc_parts) if desc_parts else ""

        return SearchResult(
            title=display_title,
            download_link=download_url,
            source_url=source_url,
            published_date=year_match.group(0) if year_match else None,
            category=category,
            description=description or detail.description[:200] or None,
        )

    async def _process_entry(
        self,
        entry: dict[str, str],
        sem: asyncio.Semaphore,
        category: int | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Fetch detail page for one series and build result."""
        async with sem:
            detail = await self._fetch_detail(entry["slug"], season)

        sr = self._build_search_result(entry, detail, season=season, episode=episode)
        if sr is None:
            return None

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
        """Search Burning Series by matching series names from the full listing.

        Fetches /andere-serien to get all series (cached), filters by query,
        then fetches detail pages for matching series.
        """
        if not query:
            return []

        # bs.to only has TV series (5xxx)
        if category is not None and not (5000 <= category < 6000):
            return []

        await self._ensure_client()
        await self._verify_domain()

        all_series = await self._fetch_series_listing()
        if not all_series:
            return []

        # Filter by query (all words must match)
        matching = [s for s in all_series if _match_query(query, s["title"])]

        if not matching:
            return []

        # Limit detail page fetches
        matching = matching[:_MAX_SERIES_DETAIL]

        # Fetch detail pages with bounded concurrency
        sem = self._new_semaphore()
        tasks = [
            self._process_entry(e, sem, category, season=season, episode=episode)
            for e in matching
        ]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results[: self.effective_max_results]


plugin = BurningSeriesPlugin()
