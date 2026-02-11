"""kinoger.com Python plugin for Scavengarr.

Scrapes kinoger.com (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /index.php?do=search&subaction=search&story={query} for keyword search
- Pagination via search_start={N} parameter (12 results/page, up to 84 pages)
- Detail page scraping for stream tabs (iframe URLs from tab sections)
- Series detection from badge text (S01-04, S01E01-02) or "Serie" in genres
- Category filtering (Movies/TV/Anime)
- Bounded concurrency for detail page scraping

Multi-domain support: kinoger.com (primary), kinoger.to (fallback).
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
    "kinoger.com",
    "kinoger.to",
]

_BASE_URL = f"https://{_DOMAINS[0]}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000
_MAX_PAGES = 84  # 12 results/page → 84 pages for ~1000

_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

# Series badge pattern: S01, S01-04, S01E01-02, etc.
_SERIES_BADGE_RE = re.compile(r"S\d+", re.IGNORECASE)


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


def _detect_series(badge: str, genres: list[str]) -> bool:
    """Detect if an item is a series based on badge text or genres."""
    if _SERIES_BADGE_RE.search(badge):
        return True
    lower_genres = [g.lower() for g in genres]
    return "serie" in lower_genres or "serien" in lower_genres


def _detect_category(genres: list[str], is_series: bool) -> int:
    """Determine Torznab category from genres and series flag."""
    if is_series:
        lower_genres = [g.lower() for g in genres]
        if "anime" in lower_genres or "animation" in lower_genres:
            return 5070
        return 5000
    lower_genres = [g.lower() for g in genres]
    if "anime" in lower_genres or "animation" in lower_genres:
        return 5070
    return 2000


def _clean_title(title: str) -> str:
    """Strip common suffixes like ' Film', ' Serie', trailing year."""
    title = title.strip()
    for suffix in (" Film", " Serie", " film", " serie"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    # Strip trailing year in parens: "Title (2023)"
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
    """Parse kinoger.com DLE search result page.

    Each result is a card with structure like::

        <div class="shortstory-in">
          <div class="shortstory-poster">
            <a href="/stream/12345-title.html">
              <img src="..." alt="Title">
            </a>
            <span class="badge">WEBRip</span>
            <span class="badge">S01-04</span>
          </div>
          <a class="shortstory-title" href="/stream/12345-title.html">Title</a>
          <div class="shortstory-content">
            <ul class="breadcrumbs">
              <li>Stream</li>
              <li>Animation</li>
              <li>Action</li>
            </ul>
          </div>
        </div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

        # Card tracking
        self._in_card = False
        self._card_div_depth = 0

        # Poster area (for badges)
        self._in_poster = False
        self._poster_div_depth = 0

        # Title link
        self._in_title_a = False
        self._current_title = ""
        self._current_url = ""

        # Badge tracking
        self._in_badge_span = False
        self._badge_text = ""
        self._badges: list[str] = []

        # Breadcrumb tracking (genres)
        self._in_breadcrumbs = False
        self._in_breadcrumb_li = False
        self._breadcrumb_text = ""
        self._genres: list[str] = []

        # Description
        self._in_desc = False
        self._desc_text = ""

    def _reset_card(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._badges = []
        self._genres = []
        self._desc_text = ""

    def _emit_card(self) -> None:
        if not self._current_title or not self._current_url:
            return

        quality = ""
        series_badge = ""
        for badge in self._badges:
            text = badge.strip()
            if _SERIES_BADGE_RE.search(text):
                series_badge = text
            elif text.upper() in (
                "WEBRIP",
                "BDRIP",
                "CAMRIP",
                "TS",
                "HD",
                "SD",
                "4K",
                "HDTV",
            ):
                quality = text

        # Filter out "Stream" from genres
        genres = [g for g in self._genres if g.lower() != "stream"]
        is_series = _detect_series(series_badge, genres)

        self.results.append(
            {
                "title": _clean_title(self._current_title),
                "url": self._current_url,
                "genres": genres,
                "quality": quality,
                "badge": series_badge,
                "is_series": is_series,
            }
        )

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        href = attr_dict.get("href", "") or ""

        # Card boundary: <div class="shortstory-in">
        if tag == "div":
            if self._in_card:
                self._card_div_depth += 1
            elif "shortstory-in" in classes:
                self._in_card = True
                self._card_div_depth = 0
                self._reset_card()

            # Poster area inside card
            if self._in_card:
                if self._in_poster:
                    self._poster_div_depth += 1
                elif "shortstory-poster" in classes:
                    self._in_poster = True
                    self._poster_div_depth = 0

        if not self._in_card:
            return

        # Title link: <a class="shortstory-title" href="...">
        if tag == "a" and "shortstory-title" in classes:
            self._in_title_a = True
            if href:
                self._current_url = urljoin(self._base_url, href)
            self._current_title = ""

        # Poster link (fallback for URL if no title link found yet)
        if tag == "a" and self._in_poster and not self._current_url:
            if href and "/stream/" in href:
                self._current_url = urljoin(self._base_url, href)

        # Badge span
        if tag == "span" and "badge" in classes and self._in_card:
            self._in_badge_span = True
            self._badge_text = ""

        # Breadcrumbs: <ul class="breadcrumbs">
        if tag == "ul" and "breadcrumbs" in classes:
            self._in_breadcrumbs = True

        if tag == "li" and self._in_breadcrumbs:
            self._in_breadcrumb_li = True
            self._breadcrumb_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data

        if self._in_badge_span:
            self._badge_text += data

        if self._in_breadcrumb_li:
            self._breadcrumb_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "a" and self._in_title_a:
            self._in_title_a = False
            self._current_title = self._current_title.strip()

        if tag == "span" and self._in_badge_span:
            self._in_badge_span = False
            text = self._badge_text.strip()
            if text:
                self._badges.append(text)

        if tag == "li" and self._in_breadcrumb_li:
            self._in_breadcrumb_li = False
            text = self._breadcrumb_text.strip()
            if text:
                self._genres.append(text)

        if tag == "ul" and self._in_breadcrumbs:
            self._in_breadcrumbs = False

        if tag == "div":
            if self._in_poster:
                if self._poster_div_depth > 0:
                    self._poster_div_depth -= 1
                else:
                    self._in_poster = False

            if self._in_card:
                if self._card_div_depth > 0:
                    self._card_div_depth -= 1
                else:
                    self._in_card = False
                    self._emit_card()


class _DetailPageParser(HTMLParser):
    """Parse kinoger.com detail page for stream tabs and metadata.

    Stream tabs have structure::

        <div class="tabs">
          <input id="tab1" type="radio" name="tab-control" checked>
          <label for="tab1" title="Stream HD+">Stream HD+</label>
          ...
          <section id="content1">
            <iframe src="https://fsst.online/embed/..."></iframe>
          </section>
          <section id="content2">
            <iframe src="https://kinoger.p2pplay.pro/..."></iframe>
          </section>
        </div>

    Metadata from the page body:
    - Year from text or meta
    - Genres from breadcrumbs
    - Description from content area
    - IMDb rating if present
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Stream tabs
        self.stream_links: list[dict[str, str]] = []
        self._tab_labels: dict[str, str] = {}  # tab id → label text
        self._current_label_for = ""
        self._in_label = False
        self._label_title_attr = ""
        self._label_text = ""

        # Section/iframe tracking
        self._in_section = False
        self._section_id = ""
        self._section_iframe_src = ""

        # Metadata
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.quality = ""
        self.imdb_rating = ""
        self.runtime = ""
        self.is_series = False
        self.badge = ""

        # Title tracking (h1)
        self._in_h1 = False
        self._h1_text = ""

        # Breadcrumb tracking (genres)
        self._in_breadcrumbs = False
        self._in_breadcrumb_li = False
        self._breadcrumb_text = ""
        self._genres: list[str] = []

        # Badge tracking
        self._in_badge_span = False
        self._badge_text = ""

        # Description tracking
        self._in_desc = False
        self._desc_div_depth = 0
        self._desc_text = ""

        # IMDb tracking
        self._in_imdb_span = False
        self._imdb_text = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Tab labels: <label for="tab1" title="Stream HD+">
        if tag == "label":
            for_attr = attr_dict.get("for", "") or ""
            if for_attr.startswith("tab"):
                self._in_label = True
                self._current_label_for = for_attr
                self._label_title_attr = attr_dict.get("title", "") or ""
                self._label_text = ""

        # Sections with iframes: <section id="content1">
        if tag == "section":
            section_id = attr_dict.get("id", "") or ""
            if section_id.startswith("content"):
                self._in_section = True
                self._section_id = section_id
                self._section_iframe_src = ""

        # Iframe inside section
        if tag == "iframe" and self._in_section:
            src = attr_dict.get("src", "") or ""
            if src:
                self._section_iframe_src = src

        # h1
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # Breadcrumbs: <ul class="breadcrumbs">
        if tag == "ul" and "breadcrumbs" in classes:
            self._in_breadcrumbs = True

        if tag == "li" and self._in_breadcrumbs:
            self._in_breadcrumb_li = True
            self._breadcrumb_text = ""

        # Badge span
        if tag == "span" and "badge" in classes:
            self._in_badge_span = True
            self._badge_text = ""

        # Description area: <div class="full-text">
        if tag == "div":
            if self._in_desc:
                self._desc_div_depth += 1
            elif "full-text" in classes:
                self._in_desc = True
                self._desc_div_depth = 0
                self._desc_text = ""

        # IMDb rating span: <span class="imdb">
        if tag == "span" and "imdb" in classes:
            self._in_imdb_span = True
            self._imdb_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_label:
            self._label_text += data

        if self._in_h1:
            self._h1_text += data

        if self._in_breadcrumb_li:
            self._breadcrumb_text += data

        if self._in_badge_span:
            self._badge_text += data

        if self._in_desc:
            self._desc_text += data

        if self._in_imdb_span:
            self._imdb_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "label" and self._in_label:
            self._in_label = False
            label_text = self._label_title_attr or self._label_text.strip()
            if self._current_label_for and label_text:
                # Map tab ID → content ID: "tab1" → "content1"
                num = self._current_label_for.replace("tab", "")
                content_id = f"content{num}"
                self._tab_labels[content_id] = label_text

        if tag == "section" and self._in_section:
            self._in_section = False
            if self._section_iframe_src:
                label = self._tab_labels.get(self._section_id, "")
                domain = _domain_from_url(self._section_iframe_src)
                self.stream_links.append(
                    {
                        "hoster": domain,
                        "link": self._section_iframe_src,
                        "label": label,
                    }
                )

        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = _clean_title(self._h1_text)

        if tag == "li" and self._in_breadcrumb_li:
            self._in_breadcrumb_li = False
            text = self._breadcrumb_text.strip()
            if text:
                self._genres.append(text)

        if tag == "ul" and self._in_breadcrumbs:
            self._in_breadcrumbs = False
            # Filter out "Stream" from genres
            self.genres = [g for g in self._genres if g.lower() != "stream"]

        if tag == "span" and self._in_badge_span:
            self._in_badge_span = False
            text = self._badge_text.strip()
            if text:
                self.badge = text
                if _SERIES_BADGE_RE.search(text):
                    self.is_series = True
                elif text.upper() in (
                    "WEBRIP",
                    "BDRIP",
                    "CAMRIP",
                    "TS",
                    "HD",
                    "SD",
                    "4K",
                    "HDTV",
                ):
                    self.quality = text

        if tag == "div" and self._in_desc:
            if self._desc_div_depth > 0:
                self._desc_div_depth -= 1
            else:
                self._in_desc = False
                self.description = self._desc_text.strip()

        if tag == "span" and self._in_imdb_span:
            self._in_imdb_span = False
            text = self._imdb_text.strip()
            m = re.search(r"(\d+\.?\d*)", text)
            if m:
                self.imdb_rating = m.group(1)

    def finalize(self) -> None:
        """Post-processing: detect series from genres, extract year/runtime."""
        lower_genres = [g.lower() for g in self.genres]
        if "serie" in lower_genres or "serien" in lower_genres:
            self.is_series = True

        # Try to extract year from title or description
        if not self.year:
            m = re.search(r"\b(19|20)\d{2}\b", self.description)
            if m:
                self.year = m.group(0)


class KinogerPlugin:
    """Python plugin for kinoger.com using httpx."""

    name = "kinoger"
    version = "1.0.0"
    mode = "httpx"

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
                    log.info("kinoger_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("kinoger_no_domain_reachable", fallback=_DOMAINS[0])

    async def _search_page(
        self,
        query: str,
        page: int = 1,
    ) -> list[dict[str, str | list[str] | bool]]:
        """Fetch a search results page.

        DLE CMS search uses::
            GET /index.php?do=search&subaction=search&story={query}

        Pagination::
            GET /index.php?do=search&subaction=search&search_start={N}&story={query}
        """
        client = await self._ensure_client()

        params: dict[str, str] = {
            "do": "search",
            "subaction": "search",
            "story": query,
        }
        if page > 1:
            params["search_start"] = str(page)

        try:
            resp = await client.get(
                f"{self.base_url}/index.php",
                params=params,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "kinoger_search_failed",
                query=query,
                page=page,
                error=str(exc),
            )
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        log.info(
            "kinoger_search_page",
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
        """Scrape a detail page for stream tabs and metadata."""
        client = await self._ensure_client()
        detail_url = str(result["url"])

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "kinoger_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)
        parser.finalize()

        if not parser.stream_links:
            log.debug("kinoger_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        is_series = parser.is_series or bool(result.get("is_series", False))
        quality = parser.quality or str(result.get("quality", ""))
        category = _detect_category(genres, is_series)

        description_parts: list[str] = []
        if genres:
            description_parts.append(", ".join(genres))
        if parser.year:
            description_parts.append(f"({parser.year})")
        if parser.description:
            description_parts.append(parser.description)
        description = " ".join(description_parts) if description_parts else ""

        metadata: dict[str, str] = {
            "year": parser.year,
            "genres": ", ".join(genres),
            "quality": quality,
            "imdb_rating": parser.imdb_rating,
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
    ) -> list[SearchResult]:
        """Search kinoger.com and return results with stream links."""
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        all_items = await self._search_all_pages(query)
        if not all_items:
            return []

        # Scrape detail pages with bounded concurrency
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

        # Filter by category if specified
        if category is not None:
            results = _filter_by_category(results, category)

        return results

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = KinogerPlugin()
