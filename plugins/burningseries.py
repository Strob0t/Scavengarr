"""bs.to (Burning Series) Python plugin for Scavengarr.

Scrapes bs.to and mirror domains (German TV series streaming aggregator) with:
- httpx for all requests (server-rendered pages, no JS challenges)
- Series listing from /andere-serien (all series grouped by genre)
- Detail pages at /serie/{slug} with season/episode/hoster info
- TV series only: Anime, Comedy, Drama, Documentary, etc.

Multi-domain support with automatic fallback.
No authentication required for browsing.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

# Known domains in priority order (all serve identical content).
_DOMAINS = [
    "bs.to",
    "burning-series.io",
    "burning-series.net",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_MAX_SERIES_DETAIL = 50  # Max series to fetch detail pages for

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


class _SeriesListParser(HTMLParser):
    """Parse the /andere-serien page to extract series names, URL slugs, genres.

    HTML structure::

        <div class="genre">
          <span><strong>Abenteuer</strong></span>
          <ul>
            <li><a href="serie/Name-Slug" title="Title">Title</a></li>
            ...
          </ul>
        </div>
    """

    def __init__(self) -> None:
        super().__init__()
        self.series: list[dict[str, str]] = []

        # Genre tracking
        self._in_genre_div = False
        self._genre_div_depth = 0
        self._in_genre_strong = False
        self._current_genre = ""

        # Link tracking
        self._in_li_a = False
        self._current_href = ""
        self._current_title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if tag == "div" and "genre" in classes:
            self._in_genre_div = True
            self._genre_div_depth = 0
            return

        if tag == "div" and self._in_genre_div:
            self._genre_div_depth += 1

        if not self._in_genre_div:
            return

        if tag == "strong":
            self._in_genre_strong = True
            self._current_genre = ""

        if tag == "a":
            href = attr_dict.get("href", "") or ""
            if "serie/" in href:
                self._in_li_a = True
                self._current_href = href
                self._current_title = ""

    def handle_data(self, data: str) -> None:
        if self._in_genre_strong:
            self._current_genre += data

        if self._in_li_a:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong" and self._in_genre_strong:
            self._in_genre_strong = False

        if tag == "a" and self._in_li_a:
            self._in_li_a = False
            title = self._current_title.strip()
            href = self._current_href.strip()
            if title and href:
                # Extract slug from href like "serie/Breaking-Bad"
                slug = href.replace("serie/", "").strip("/")
                self.series.append(
                    {
                        "title": title,
                        "slug": slug,
                        "genre": self._current_genre.strip(),
                    }
                )

        if tag == "div" and self._in_genre_div:
            if self._genre_div_depth > 0:
                self._genre_div_depth -= 1
            else:
                self._in_genre_div = False


class _SeriesDetailParser(HTMLParser):
    """Parse a series detail page at /serie/{slug}.

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
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.genres: list[str] = []
        self.year = ""
        self.season_count = 0
        self.episode_count = 0

        # h2 tracking
        self._in_h2 = False
        self._in_h2_small = False
        self._h2_text = ""

        # Description: first <p> after <h2> inside sp_left
        self._in_sp_left = False
        self._sp_left_depth = 0
        self._got_h2 = False
        self._in_desc_p = False
        self._got_description = False

        # Info section tracking
        self._in_infos = False
        self._infos_depth = 0
        self._current_info_label = ""
        self._in_info_span_label = False
        self._in_info_p = False
        self._info_p_depth = 0
        self._in_info_span = False
        self._in_info_em = False

        # Seasons tracking
        self._in_seasons = False
        self._seasons_depth = 0

        # Episodes tracking
        self._in_episodes_table = False
        self._in_episode_tr = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        elem_id = attr_dict.get("id", "") or ""

        # Track sp_left div
        if tag == "div" and elem_id == "sp_left":
            self._in_sp_left = True
            self._sp_left_depth = 0
            return

        if tag == "div" and self._in_sp_left:
            self._sp_left_depth += 1

        # h2 for title
        if tag == "h2" and self._in_sp_left and not self._got_h2:
            self._in_h2 = True
            self._h2_text = ""

        if tag == "small" and self._in_h2:
            self._in_h2_small = True

        # First <p> after <h2> = description
        if (
            tag == "p"
            and self._in_sp_left
            and self._got_h2
            and not self._got_description
            and not self._in_infos
        ):
            self._in_desc_p = True

        # Infos section
        if tag == "div" and "infos" in classes:
            self._in_infos = True
            self._infos_depth = 0
            return

        if tag == "div" and self._in_infos:
            self._infos_depth += 1

        # Info label <span> (direct child of info div, e.g., "Genres")
        if tag == "span" and self._in_infos and not self._in_info_p:
            self._in_info_span_label = True
            self._current_info_label = ""

        # Info value <p>
        if tag == "p" and self._in_infos:
            self._in_info_p = True
            self._info_p_depth = 0

        # Genre spans inside info <p>
        if tag == "span" and self._in_info_p:
            self._in_info_span = True

        # Year <em> inside info <p>
        if tag == "em" and self._in_info_p:
            self._in_info_em = True

        # Seasons section
        if tag == "div" and elem_id == "seasons":
            self._in_seasons = True
            self._seasons_depth = 0
            return

        if tag == "div" and self._in_seasons:
            self._seasons_depth += 1

        if tag == "li" and self._in_seasons:
            self.season_count += 1

        # Episodes table
        if tag == "table" and "episodes" in classes:
            self._in_episodes_table = True

        if tag == "tr" and self._in_episodes_table:
            self._in_episode_tr = True
            self.episode_count += 1

    def handle_data(self, data: str) -> None:
        if self._in_h2 and not self._in_h2_small:
            self._h2_text += data

        if self._in_desc_p:
            self.description += data

        if self._in_info_span_label:
            self._current_info_label += data

        if self._in_info_span and self._current_info_label.strip() == "Genres":
            text = data.strip().rstrip(",")
            if text:
                self.genres.append(text)

        if self._in_info_em and self._current_info_label.strip() == "Produktionsjahre":
            self.year += data.strip()

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "small" and self._in_h2_small:
            self._in_h2_small = False

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            self._got_h2 = True
            self.title = self._h2_text.strip()

        if tag == "p" and self._in_desc_p:
            self._in_desc_p = False
            self._got_description = True
            self.description = self.description.strip()

        if tag == "span" and self._in_info_span_label and not self._in_info_p:
            self._in_info_span_label = False

        if tag == "span" and self._in_info_span:
            self._in_info_span = False

        if tag == "em" and self._in_info_em:
            self._in_info_em = False

        if tag == "p" and self._in_info_p:
            self._in_info_p = False
            self._current_info_label = ""

        if tag == "div" and self._in_infos:
            if self._infos_depth > 0:
                self._infos_depth -= 1
            else:
                self._in_infos = False

        if tag == "div" and self._in_sp_left and not self._in_infos:
            if self._sp_left_depth > 0:
                self._sp_left_depth -= 1
            else:
                self._in_sp_left = False

        if tag == "div" and self._in_seasons:
            if self._seasons_depth > 0:
                self._seasons_depth -= 1
            else:
                self._in_seasons = False

        if tag == "table" and self._in_episodes_table:
            self._in_episodes_table = False

        if tag == "tr" and self._in_episode_tr:
            self._in_episode_tr = False


def _genre_to_category(genre: str) -> int:
    """Map a bs.to genre name to a Torznab category."""
    key = genre.lower().strip()
    return _GENRE_CATEGORY.get(key, 5000)


def _match_query(query: str, title: str) -> bool:
    """Check if all query words appear in the title (case-insensitive)."""
    query_lower = query.lower()
    title_lower = title.lower()
    return all(word in title_lower for word in query_lower.split())


class BurningSeriesPlugin:
    """Python plugin for bs.to / Burning Series using httpx."""

    name = "burningseries"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self.base_url: str = f"https://{_DOMAINS[0]}"
        self._domain_verified = False
        self._series_cache: list[dict[str, str]] | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create httpx client if not already running."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def _verify_domain(self) -> None:
        """Find and cache a working domain from the fallback list."""
        if self._domain_verified:
            return

        client = await self._ensure_client()
        for domain in _DOMAINS:
            url = f"https://{domain}/"
            try:
                resp = await client.head(url, timeout=5.0)
                if resp.status_code == 200:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    log.info("burningseries_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("burningseries_no_domain_reachable", fallback=_DOMAINS[0])

    async def _fetch_series_listing(self) -> list[dict[str, str]]:
        """Fetch and parse the full series listing, cached for plugin lifetime."""
        if self._series_cache is not None:
            return self._series_cache

        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/andere-serien",
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("burningseries_listing_failed", error=str(exc))
            return []

        parser = _SeriesListParser()
        parser.feed(resp.text)

        # Deduplicate by slug (a series can appear in multiple genre sections)
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for entry in parser.series:
            slug = entry["slug"]
            if slug not in seen:
                seen.add(slug)
                unique.append(entry)

        self._series_cache = unique
        log.info(
            "burningseries_listing_loaded",
            total=len(parser.series),
            unique=len(unique),
        )
        return unique

    async def _fetch_detail(self, slug: str) -> _SeriesDetailParser:
        """Fetch and parse a series detail page."""
        client = await self._ensure_client()

        try:
            resp = await client.get(f"{self.base_url}/serie/{slug}")
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("burningseries_detail_failed", slug=slug, error=str(exc))
            return _SeriesDetailParser()

        parser = _SeriesDetailParser()
        parser.feed(resp.text)

        log.info(
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
    ) -> SearchResult:
        """Build a SearchResult from listing entry + detail page data."""
        title = detail.title or listing_entry["title"]
        year = detail.year
        slug = listing_entry["slug"]
        genre = listing_entry.get("genre", "")
        source_url = f"{self.base_url}/serie/{slug}"

        # Build display title with year
        display_title = f"{title} ({year})" if year else title

        # Category from genre
        category = _genre_to_category(genre)

        # Build description
        desc_parts: list[str] = []
        if detail.genres:
            desc_parts.append(", ".join(detail.genres))
        if detail.season_count:
            desc_parts.append(f"{detail.season_count} Staffeln")
        if detail.episode_count:
            desc_parts.append(f"{detail.episode_count} Episoden (S1)")
        description = " | ".join(desc_parts) if desc_parts else ""

        return SearchResult(
            title=display_title,
            download_link=source_url,
            source_url=source_url,
            published_date=re.search(r"\d{4}", year).group(0) if year else None,
            category=category,
            description=description or detail.description[:200] or None,
        )

    async def _process_entry(
        self,
        entry: dict[str, str],
        sem: asyncio.Semaphore,
        category: int | None,
    ) -> SearchResult | None:
        """Fetch detail page for one series and build result."""
        async with sem:
            detail = await self._fetch_detail(entry["slug"])

        sr = self._build_search_result(entry, detail)

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
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        tasks = [self._process_entry(e, sem, category) for e in matching]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= _MAX_RESULTS:
                    break

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = BurningSeriesPlugin()
