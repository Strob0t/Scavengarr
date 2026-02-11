"""streamkiste.taxi Python plugin for Scavengarr.

Scrapes streamkiste.taxi (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /index.php?do=search&subaction=search&story={query} for page 1
- POST /index.php?do=search for page 2+ with form data
- 21 results per page, up to 48 pages for ~1000 results
- Detail page scraping for stream links (onclick-based URLs in a.streams)
- Series detection from genre text ("Serien" in release info)
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Multi-domain support: streamkiste.taxi (primary), .tv, .sx, .al, .city.
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
    "streamkiste.taxi",
    "streamkiste.tv",
    "streamkiste.sx",
    "streamkiste.al",
    "streamkiste.city",
]

_BASE_URL = f"https://{_DOMAINS[0]}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_RESULTS_PER_PAGE = 21
_MAX_PAGES = 48  # 21 results/page → 48 pages for ~1000

_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

_SERIES_KEYWORDS = frozenset({"serie", "serien"})

# Regex to extract URL from onclick="window.open('...')"
_ONCLICK_URL_RE = re.compile(r"window\.open\(['\"]([^'\"]+)['\"]\)")


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
    return bool(lower_genres & _SERIES_KEYWORDS)


def _detect_category(genres: list[str], is_series: bool) -> int:
    """Determine Torznab category from genres and series flag."""
    lower_genres = {g.lower() for g in genres}
    if "anime" in lower_genres or "animation" in lower_genres:
        return 5070
    if is_series:
        return 5000
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


def _extract_onclick_url(onclick: str) -> str:
    """Extract URL from onclick=\"window.open('...')\" attribute."""
    m = _ONCLICK_URL_RE.search(onclick)
    return m.group(1) if m else ""


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
          <div class="movie-title">
            <a href="/film/12345-title.html" title="Title">Title</a>
          </div>
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

        if tag == "div":
            if self._in_release_div:
                self._in_release_div = False

            if self._in_movie_title_div:
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
    """Parse streamkiste.taxi detail page for download links and metadata.

    Download links have structure::

        <a class="streams" onclick="window.open('https://host.com/id')">
          <span class="streaming">Supervideo</span>
          <mark>1080p</mark>
          <span>1.0GB</span>
        </a>

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

        # Download links
        self.stream_links: list[dict[str, str]] = []
        self._in_stream_a = False
        self._stream_url = ""
        self._stream_hoster = ""
        self._stream_quality = ""
        self._stream_size = ""

        self._in_streaming_span = False
        self._streaming_text = ""
        self._in_mark = False
        self._mark_text = ""
        self._in_stream_size_span = False
        self._size_text = ""

        # Metadata
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.imdb_rating = ""
        self.is_series = False

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

    def handle_starttag(  # noqa: C901
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Stream link: <a class="streams" onclick="window.open('...')">
        if tag == "a" and "streams" in classes:
            onclick = attr_dict.get("onclick", "") or ""
            url = _extract_onclick_url(onclick)
            if url:
                self._in_stream_a = True
                self._stream_url = url
                self._stream_hoster = ""
                self._stream_quality = ""
                self._stream_size = ""

        if self._in_stream_a:
            if tag == "span" and "streaming" in classes:
                self._in_streaming_span = True
                self._streaming_text = ""
            elif tag == "span" and not self._in_streaming_span:
                self._in_stream_size_span = True
                self._size_text = ""
            if tag == "mark":
                self._in_mark = True
                self._mark_text = ""

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
        if tag == "span" and self._in_average and not self._in_stream_a:
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

        if self._in_streaming_span:
            self._streaming_text += data

        if self._in_mark:
            self._mark_text += data

        if self._in_stream_size_span:
            self._size_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        # Stream link end
        if tag == "a" and self._in_stream_a:
            self._in_stream_a = False
            if self._stream_url:
                hoster = self._stream_hoster.strip() or _domain_from_url(
                    self._stream_url
                )
                self.stream_links.append(
                    {
                        "hoster": hoster,
                        "link": self._stream_url,
                        "quality": self._stream_quality.strip(),
                        "size": self._stream_size.strip(),
                    }
                )

        if tag == "span" and self._in_streaming_span:
            self._in_streaming_span = False
            self._stream_hoster = self._streaming_text.strip()

        if tag == "span" and self._in_stream_size_span:
            self._in_stream_size_span = False
            self._stream_size = self._size_text.strip()

        if tag == "mark" and self._in_mark:
            self._in_mark = False
            self._stream_quality = self._mark_text.strip()

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
            text = self._average_text.strip()
            m = re.search(r"(\d+\.?\d*)", text)
            if m:
                self.imdb_rating = m.group(1)

        if tag == "div":
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

    def finalize(self) -> None:
        """Post-processing: detect series from genres."""
        self.is_series = _detect_series(self.genres)


class StreamkistePlugin:
    """Python plugin for streamkiste.taxi using httpx."""

    name = "streamkiste"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"

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
                if resp.status_code == 200:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    log.info("streamkiste_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("streamkiste_no_domain_reachable", fallback=_DOMAINS[0])

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
        client = await self._ensure_client()

        try:
            if page == 1:
                params: dict[str, str] = {
                    "do": "search",
                    "subaction": "search",
                    "story": query,
                }
                resp = await client.get(
                    f"{self.base_url}/index.php",
                    params=params,
                )
            else:
                form_data: dict[str, str] = {
                    "do": "search",
                    "subaction": "search",
                    "search_start": str(page),
                    "result_from": str((page - 1) * _RESULTS_PER_PAGE + 1),
                    "story": query,
                }
                resp = await client.post(
                    f"{self.base_url}/index.php?do=search",
                    data=form_data,
                )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "streamkiste_search_failed",
                query=query,
                page=page,
                error=str(exc),
            )
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        log.info(
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
        """Fetch search results with pagination up to _MAX_RESULTS."""
        all_results: list[dict[str, str | list[str] | bool]] = []

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
        result: dict[str, str | list[str] | bool],
    ) -> SearchResult | None:
        """Scrape a detail page for download links and metadata."""
        client = await self._ensure_client()
        detail_url = str(result["url"])

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "streamkiste_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)
        parser.finalize()

        if not parser.stream_links:
            log.debug("streamkiste_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        is_series = parser.is_series or bool(result.get("is_series", False))
        year = parser.year or str(result.get("year", ""))
        category = _detect_category(genres, is_series)

        quality = ""
        for link in parser.stream_links:
            if link.get("quality"):
                quality = link["quality"]
                break

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
    ) -> list[SearchResult]:
        """Search streamkiste.taxi and return results with stream links."""
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)

        async def _bounded(
            r: dict[str, str | list[str] | bool],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in all_items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        if category is not None:
            results = _filter_by_category(results, category)

        return results

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = StreamkistePlugin()
