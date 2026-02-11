"""streamcloud.plus Python plugin for Scavengarr.

Scrapes streamcloud.plus (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /?do=search&subaction=search&story={query} for keyword search
- POST-based pagination via search_start={N}&result_from={offset}
  (12 results/page, up to 84 pages for ~1000 results)
- Detail page scraping for stream/hoster links:
  - Movies: hosters injected via meinecloud.click script (window.open URLs)
  - Series: season/episode tabs with data-link attributes on <li> elements
- Series detection from detail page structure (season/episode tabs)
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Multi-domain support: streamcloud.plus (primary), streamcloud.my (fallback).
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_DOMAINS = [
    "streamcloud.plus",
    "streamcloud.my",
]

_BASE_URL = f"https://{_DOMAINS[0]}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_RESULTS_PER_PAGE = 12
_MAX_PAGES = 84  # 12 results/page → 84 pages for ~1000

_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

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


def _filter_by_category(
    results: list[SearchResult],
    category: int,
) -> list[SearchResult]:
    """Filter results by Torznab category type."""
    if category in _TV_CATEGORIES:
        return [r for r in results if r.category >= 5000]
    if category in _MOVIE_CATEGORIES:
        return [r for r in results if r.category < 5000]
    return results


def _detect_series(genres: list[str]) -> bool:
    """Detect if an item is a series based on genres."""
    lower_genres = {g.lower() for g in genres}
    return bool(lower_genres & _SERIES_GENRES)


def _detect_category(genres: list[str], is_series: bool) -> int:
    """Determine Torznab category from genres and series flag."""
    lower_genres = {g.lower() for g in genres}
    if is_series:
        if "anime" in lower_genres or "animation" in lower_genres:
            return 5070
        return 5000
    if "anime" in lower_genres or "animation" in lower_genres:
        return 5070
    return 2000


def _clean_title(title: str) -> str:
    """Strip common suffixes and trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    return title.strip()


def _domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        return parts[0] if parts and parts[0] else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


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
    """Parse streamcloud.plus detail page for metadata and stream links.

    Movies have hosters injected via meinecloud.click script::

        <a onclick="window.open( 'https://supervideo.cc/...' )" class="streams">
          <span class="streaming">Supervideo</span>
          <mark>1080p</mark>
          <span>1.2GB</span>
        </a>

    Series have season/episode tabs::

        <div id="season-1">
          <ul>
            <li>
              <a data-link="https://supervideo.cc/embed-..." data-num="1x1"
                 data-title="Episode 1">1</a>
              <div class="mirrors">
                <a data-m="supervideo" data-link="https://supervideo.cc/...">
                  Supervideo</a>
                <a data-m="streamtape" data-link="/player/...">Streamtape</a>
              </div>
            </li>
          </ul>
        </div>

    Metadata fields::

        <strong>Genres:</strong> <span>Action / Adventure</span>
        <strong>Veröffentlicht:</strong> <a>2024</a>
        <strong>Spielzeit:</strong> <span>121 min</span>
        IMDb link: <a href="https://www.imdb.com/title/ttXXXXX/">6.1/10</a>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Stream links (movies)
        self.stream_links: list[dict[str, str]] = []

        # Movie hoster tracking (onclick window.open links)
        self._in_streams_a = False
        self._current_stream_url = ""
        self._in_streaming_span = False
        self._streaming_hoster_name = ""
        self._in_quality_mark = False
        self._quality_text = ""
        self._in_size_span = False
        self._size_text = ""

        # Series episode/mirror links (data-link attributes)
        self._series_links: list[dict[str, str]] = []
        self._has_season_tabs = False

        # Metadata
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.quality = ""
        self.imdb_rating = ""
        self.imdb_id = ""
        self.runtime = ""
        self.is_series = False

        # Description tracking
        self._in_desc_p = False
        self._desc_text = ""

        # Metadata field tracking
        self._last_strong_text = ""
        self._in_strong = False
        self._in_genre_span = False
        self._genre_text = ""
        self._in_year_a = False
        self._year_text = ""
        self._in_runtime_span = False
        self._runtime_text = ""

        # IMDb link tracking
        self._in_imdb_a = False
        self._imdb_text = ""

        # HD | Deutsch quality tracking
        self._in_quality_div = False
        self._quality_div_text = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # -- Movie hosters: <a class="streams" onclick="window.open('URL')"> --
        if tag == "a" and "streams" in classes:
            onclick = attr_dict.get("onclick", "") or ""
            m = re.search(r"window\.open\(\s*'([^']+)'\s*\)", onclick)
            if m:
                self._in_streams_a = True
                self._current_stream_url = m.group(1)
                self._streaming_hoster_name = ""
                self._quality_text = ""
                self._size_text = ""

        # Hoster name inside <span class="streaming">
        if tag == "span" and "streaming" in classes and self._in_streams_a:
            self._in_streaming_span = True
            self._streaming_hoster_name = ""

        # Quality inside <mark>
        if tag == "mark" and self._in_streams_a:
            self._in_quality_mark = True
            self._quality_text = ""

        # -- Series episode links: <a data-link="..." data-num="1x1"> --
        if tag == "a":
            data_link = attr_dict.get("data-link", "") or ""
            data_num = attr_dict.get("data-num", "") or ""
            data_m = attr_dict.get("data-m", "") or ""
            data_title = attr_dict.get("data-title", "") or ""

            if data_link and data_num:
                # Episode primary link
                full_url = urljoin(self._base_url, data_link)
                self._series_links.append(
                    {
                        "hoster": _domain_from_url(full_url),
                        "link": full_url,
                        "label": f"{data_num} {data_title}".strip(),
                    }
                )
                self._has_season_tabs = True
            elif data_link and data_m:
                # Mirror link inside <div class="mirrors">
                full_url = urljoin(self._base_url, data_link)
                self._series_links.append(
                    {
                        "hoster": data_m,
                        "link": full_url,
                        "label": data_m,
                    }
                )
                self._has_season_tabs = True

        # Season tab divs
        if tag == "div":
            div_id = attr_dict.get("id", "") or ""
            if div_id.startswith("season-"):
                self._has_season_tabs = True

        # -- Metadata: <strong>Genres:</strong> --
        if tag == "strong":
            self._in_strong = True
            self._last_strong_text = ""

        # Genre text after "Genres:" strong
        if tag == "span" and self._last_strong_text == "Genres:":
            self._in_genre_span = True
            self._genre_text = ""

        # Year link after "Veröffentlicht:" strong
        if tag == "a":
            href = attr_dict.get("href", "") or ""
            if self._last_strong_text == "Veröffentlicht:" or "/xfsearch/" in href:
                if re.search(r"/xfsearch/\d{4}$", href):
                    self._in_year_a = True
                    self._year_text = ""

            # IMDb link
            if "imdb.com/title/" in href:
                self._in_imdb_a = True
                self._imdb_text = ""
                # Extract IMDb ID
                m = re.search(r"(tt\d+)", href)
                if m:
                    self.imdb_id = m.group(1)

        # Runtime span after "Spielzeit:" strong
        if tag == "span" and self._last_strong_text == "Spielzeit:":
            self._in_runtime_span = True
            self._runtime_text = ""

        # Description paragraph (first <p> inside the detail info area)
        if tag == "p" and not self._in_desc_p and not self.description:
            self._in_desc_p = True
            self._desc_text = ""

    def handle_data(self, data: str) -> None:  # noqa: C901
        if self._in_strong:
            self._last_strong_text += data

        if self._in_streaming_span:
            self._streaming_hoster_name += data

        if self._in_quality_mark:
            self._quality_text += data

        if self._in_genre_span:
            self._genre_text += data

        if self._in_year_a:
            self._year_text += data

        if self._in_imdb_a:
            self._imdb_text += data

        if self._in_runtime_span:
            self._runtime_text += data

        if self._in_desc_p:
            self._desc_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "strong" and self._in_strong:
            self._in_strong = False
            self._last_strong_text = self._last_strong_text.strip()

        # End of movie hoster anchor
        if tag == "a" and self._in_streams_a:
            self._in_streams_a = False
            if self._current_stream_url:
                hoster = self._streaming_hoster_name.strip()
                quality = self._quality_text.strip()
                size = self._size_text.strip()
                label_parts = [hoster]
                if quality:
                    label_parts.append(quality)
                if size:
                    label_parts.append(size)

                self.stream_links.append(
                    {
                        "hoster": _domain_from_url(self._current_stream_url)
                        if not hoster
                        else hoster.lower().replace(" ", ""),
                        "link": self._current_stream_url,
                        "label": " ".join(label_parts),
                        "quality": quality,
                        "size": size,
                    }
                )

        if tag == "span" and self._in_streaming_span:
            self._in_streaming_span = False

        if tag == "mark" and self._in_quality_mark:
            self._in_quality_mark = False

        if tag == "span" and self._in_genre_span:
            self._in_genre_span = False
            raw = self._genre_text.strip()
            if raw:
                self.genres = [g.strip() for g in raw.split("/") if g.strip()]

        if tag == "a" and self._in_year_a:
            self._in_year_a = False
            text = self._year_text.strip()
            if re.match(r"\d{4}$", text):
                self.year = text

        if tag == "a" and self._in_imdb_a:
            self._in_imdb_a = False
            text = self._imdb_text.strip()
            m = re.search(r"(\d+\.?\d*)/10", text)
            if m:
                self.imdb_rating = m.group(1)

        if tag == "span" and self._in_runtime_span:
            self._in_runtime_span = False
            self.runtime = self._runtime_text.strip()

        if tag == "p" and self._in_desc_p:
            self._in_desc_p = False
            text = self._desc_text.strip()
            if len(text) > 20:
                self.description = text

    def finalize(self) -> None:
        """Post-processing after parsing."""
        # Series detection: presence of season/episode tabs
        if self._has_season_tabs:
            self.is_series = True

        # Also detect from genres
        if _detect_series(self.genres):
            self.is_series = True

        # For series, use the episode links as stream_links
        if self.is_series and self._series_links and not self.stream_links:
            self.stream_links = self._series_links

        # Set quality from first stream link if not set
        if not self.quality and self.stream_links:
            for sl in self.stream_links:
                q = sl.get("quality", "")
                if q:
                    self.quality = q
                    break


class StreamcloudPlugin:
    """Python plugin for streamcloud.plus using httpx."""

    name = "streamcloud"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"
    default_language = "de"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._domain_verified = False
        self.base_url = _BASE_URL

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
                if resp.status_code < 400:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    log.info("streamcloud_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("streamcloud_no_domain_reachable", fallback=_DOMAINS[0])

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
        client = await self._ensure_client()

        if page == 1:
            params: dict[str, str] = {
                "do": "search",
                "subaction": "search",
                "story": query,
            }
            try:
                resp = await client.get(self.base_url, params=params)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "streamcloud_search_failed",
                    query=query,
                    page=page,
                    error=str(exc),
                )
                return []
        else:
            form_data = {
                "do": "search",
                "subaction": "search",
                "search_start": str(page),
                "full_search": "0",
                "result_from": str((page - 1) * _RESULTS_PER_PAGE + 1),
                "story": query,
            }
            try:
                resp = await client.post(
                    f"{self.base_url}/index.php?do=search",
                    data=form_data,
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "streamcloud_search_failed",
                    query=query,
                    page=page,
                    error=str(exc),
                )
                return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        log.info(
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
        """Fetch search results with pagination up to _MAX_RESULTS."""
        all_results: list[dict[str, str]] = []

        for page_num in range(1, _MAX_PAGES + 1):
            results = await self._search_page(query, page_num)
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= _MAX_RESULTS:
                break

        return all_results[:_MAX_RESULTS]

    async def _scrape_detail(
        self,
        result: dict[str, str],
    ) -> SearchResult | None:
        """Scrape a detail page for stream links and metadata."""
        client = await self._ensure_client()
        detail_url = result["url"]

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "streamcloud_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)
        parser.finalize()

        if not parser.stream_links:
            log.debug("streamcloud_no_streams", url=detail_url)
            return None

        title = _clean_title(result.get("title", ""))
        year = parser.year or result.get("year", "")
        genres = parser.genres
        is_series = parser.is_series
        quality = parser.quality
        category = _detect_category(genres, is_series)

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
            "quality": quality,
            "imdb_rating": parser.imdb_rating,
            "imdb_id": parser.imdb_id,
            "runtime": parser.runtime,
        }

        return SearchResult(
            title=title,
            download_link=parser.stream_links[0]["link"],
            download_links=parser.stream_links,
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
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        # Scrape detail pages with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)

        async def _bounded(r: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in all_items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        # Filter by category if specified
        if category is not None:
            results = _filter_by_category(results, category)

        return results

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = StreamcloudPlugin()
