"""hdfilme.legal Python plugin for Scavengarr.

Scrapes hdfilme.legal (German streaming site, DLE-based CMS) with:
- httpx for all requests (server-rendered HTML search + detail pages)
- GET /?story={query}&do=search&subaction=search for keyword search
- Detail page scraping for metadata (genres, year, duration, IMDb, TMDB)
- Film stream links via meinecloud.click/ddl/{imdb_id} external JS
- Series episode links via su-spoiler-content divs (direct hoster links)
- Category detection from detail page: /serien/ genre link → TV (5000)
- Bounded concurrency for detail page scraping

Mirror domains: hdfilme.legal (primary), hdfilme.my → hdfilme.app → hdfilme.uno,
hdfilme.best → www6.hdfilme.best (redirects, less reliable).
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_BASE_URL = "https://hdfilme.legal"
_MEINECLOUD_BASE = "https://meinecloud.click"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000

# Torznab category → site category path for browsing.
_CATEGORY_PATH_MAP: dict[int, str] = {
    2000: "filme1",
    5000: "serien",
}

# Site genre names → Torznab category override.
_GENRE_CATEGORY_MAP: dict[str, int] = {
    "serien": 5000,
    "animation": 2040,
    "dokumentation": 5080,
    "horror": 2040,
}


_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})


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


class _SearchResultParser(HTMLParser):
    """Parse hdfilme.legal search result cards.

    Each result card has this structure::

        <div class="item relative mt-3">
          <div class="flex flex-col h-full">
            <a class="block relative" href="/filme1/{id}-{slug}-stream.html"
               title="Title" data-tooltip-id="...">
              <figure>...</figure>
            </a>
            <a class="movie-title" title="Title"
               href="/filme1/{id}-{slug}-stream.html">
              <h3 class="..."> Title </h3>
            </a>
            <p class="..."> Title </p>
            <div class="...">
              <div class="meta ...">
                <span>2004</span>
                <i class="dot ..."></i>
                <span>20 min</span>
                <span class="... right-0 ..."> HD </span>
              </div>
            </div>
          </div>
        </div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

        # Item tracking
        self._in_item = False
        self._item_div_depth = 0

        # Title link
        self._in_movie_title_a = False
        self._current_title = ""
        self._current_url = ""

        # Meta spans (year, duration, quality)
        self._in_meta = False
        self._in_meta_span = False
        self._meta_span_text = ""
        self._meta_spans: list[str] = []

    def _reset_item(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._meta_spans = []

    def _emit_item(self) -> None:
        if not self._current_title or not self._current_url:
            return

        year = ""
        duration = ""
        quality = ""
        for span in self._meta_spans:
            text = span.strip()
            if re.match(r"^\d{4}$", text):
                year = text
            elif "min" in text.lower():
                duration = text
            elif text.upper() in ("HD", "CAM", "TS", "SD", "4K"):
                quality = text

        self.results.append(
            {
                "title": self._current_title,
                "url": self._current_url,
                "year": year,
                "duration": duration,
                "quality": quality,
            }
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Item boundary: <div class="item ...">
        if tag == "div":
            if self._in_item:
                self._item_div_depth += 1
            elif "item" in classes:
                self._in_item = True
                self._item_div_depth = 0
                self._reset_item()

        if not self._in_item:
            return

        # <a class="movie-title" ...>
        if tag == "a" and "movie-title" in classes:
            self._in_movie_title_a = True
            href = attr_dict.get("href", "") or ""
            if href:
                self._current_url = urljoin(self._base_url, href)
            self._current_title = ""

        # <div class="meta ...">
        if tag == "div" and "meta" in classes:
            self._in_meta = True

        # <span> inside meta div
        if tag == "span" and self._in_meta:
            self._in_meta_span = True
            self._meta_span_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_movie_title_a:
            self._current_title += data

        if self._in_meta_span:
            self._meta_span_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_movie_title_a:
            self._in_movie_title_a = False
            self._current_title = self._current_title.strip()

        if tag == "span" and self._in_meta_span:
            self._in_meta_span = False
            text = self._meta_span_text.strip()
            if text:
                self._meta_spans.append(text)

        if tag == "div":
            if self._in_meta:
                self._in_meta = False
            if self._in_item:
                if self._item_div_depth > 0:
                    self._item_div_depth -= 1
                else:
                    self._in_item = False
                    self._emit_item()


class _DetailPageParser(HTMLParser):
    """Parse hdfilme.legal film/series detail page.

    Extracts:
    - IMDb ID from ``<script src="meinecloud.click/ddl/{imdb_id}">``
    - IMDb ID from ``<iframe src="meinecloud.click/movie/{imdb_id}">``
    - Genres from ``<a href="/{genre}/">GenreName</a>`` in info section
    - Year, duration, quality from metadata spans
    - TMDB URL from ``<a href="themoviedb.org/...">``
    - IMDb URL from ``<a href="imdb.com/title/...">``
    - Description from h2 heading (distinguishes film/series)
    - Series detection from ``Staffel/Episode:`` in metadata or /serien/ genre
    - Series episode links from su-spoiler-content divs
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Metadata
        self.imdb_id = ""
        self.tmdb_url = ""
        self.imdb_url = ""
        self.genres: list[str] = []
        self.year = ""
        self.duration = ""
        self.quality = ""
        self.is_series = False
        self.title = ""
        self.description = ""

        # meinecloud script/iframe tracking
        self._found_meinecloud = False

        # Info section tracking
        self._in_info = False
        self._info_div_depth = 0
        self._in_genre_span = False
        self._in_genre_a = False
        self._genre_a_href = ""
        self._genre_a_text = ""
        self._in_meta_line = False
        self._meta_line_div_depth = 0
        self._in_meta_span = False
        self._meta_span_text = ""
        self._meta_spans: list[str] = []

        # H1 tracking
        self._in_h1 = False
        self._h1_text = ""

        # H2 tracking (series detection)
        self._in_h2 = False
        self._h2_text = ""

        # Description
        self._in_prose = False
        self._prose_text = ""
        self._in_prose_a = False

        # Series episode links
        self._in_spoiler_content = False
        self._spoiler_content_text = ""
        self._spoiler_season = ""
        self._in_spoiler_title = False
        self._spoiler_title_text = ""
        self.episode_links: list[dict[str, str]] = []
        self._in_episode_a = False
        self._episode_a_href = ""
        self._episode_a_text = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        href = attr_dict.get("href", "") or ""
        src = attr_dict.get("src", "") or ""

        # meinecloud.click script tag
        if tag == "script" and "meinecloud.click/ddl/" in src:
            m = re.search(r"/ddl/(tt\d+)", src)
            if m:
                self.imdb_id = m.group(1)
                self._found_meinecloud = True

        # meinecloud.click iframe
        if tag == "iframe" and "meinecloud.click/movie/" in src:
            m = re.search(r"/movie/(tt\d+)", src)
            if m and not self.imdb_id:
                self.imdb_id = m.group(1)

        # h1
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # h2
        if tag == "h2":
            self._in_h2 = True
            self._h2_text = ""

        # Info section: <div class="info md:pl-5 md:flex-grow">
        if tag == "div" and "info" in classes:
            self._in_info = True
            self._info_div_depth = 0
        elif tag == "div" and self._in_info:
            self._info_div_depth += 1

        # Genre span (first span in meta line, contains genre links)
        if self._in_info and tag == "div" and "border-b" in classes:
            self._in_meta_line = True
            self._meta_line_div_depth = 0
        elif tag == "div" and self._in_meta_line:
            self._meta_line_div_depth += 1

        # Track first span in meta line for genres
        if self._in_meta_line and tag == "span" and not self._in_genre_span:
            # Check if this is a divider span
            if "divider" not in classes and "align-text-bottom" not in classes:
                if not self.genres and not self._in_genre_span:
                    self._in_genre_span = True

        # Genre links inside the genre span
        if self._in_genre_span and tag == "a":
            self._in_genre_a = True
            self._genre_a_href = href
            self._genre_a_text = ""

        # Meta spans for year/duration/quality
        if self._in_meta_line and tag == "span":
            self._in_meta_span = True
            self._meta_span_text = ""

        # TMDB link
        if tag == "a" and "themoviedb.org" in href:
            self.tmdb_url = href
            if "/tv/" in href:
                self.is_series = True

        # IMDb link
        if tag == "a" and "imdb.com/title/" in href:
            self.imdb_url = href
            m = re.search(r"title/(tt\d+)", href)
            if m and not self.imdb_id:
                self.imdb_id = m.group(1)

        # Description prose
        if tag == "div" and "prose" in classes:
            self._in_prose = True
            self._prose_text = ""

        if self._in_prose and tag == "a":
            self._in_prose_a = True

        # Series spoiler title: <div class="su-spoiler-title" ...>
        if tag == "div" and "su-spoiler-title" in classes:
            self._in_spoiler_title = True
            self._spoiler_title_text = ""

        # Series spoiler content: <div class="su-spoiler-content" ...>
        if tag == "div" and "su-spoiler-content" in classes:
            self._in_spoiler_content = True
            self._spoiler_content_text = ""
            self.is_series = True

        # Episode links inside spoiler content
        if self._in_spoiler_content and tag == "a":
            if href and "/engine/player.php" not in href:
                self._in_episode_a = True
                self._episode_a_href = href
                self._episode_a_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data

        if self._in_h2:
            self._h2_text += data

        if self._in_genre_a:
            self._genre_a_text += data

        if self._in_meta_span:
            self._meta_span_text += data

        if self._in_prose and not self._in_prose_a:
            self._prose_text += data

        if self._in_spoiler_title:
            self._spoiler_title_text += data

        if self._in_spoiler_content:
            self._spoiler_content_text += data

        if self._in_episode_a:
            self._episode_a_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = self._h1_text.strip()
            # Remove " hdfilme" suffix
            if self.title.lower().endswith(" hdfilme"):
                self.title = self.title[: -len(" hdfilme")].strip()

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            h2 = self._h2_text.strip().lower()
            if "stream serien" in h2 or "serien kostenlos" in h2:
                self.is_series = True

        if tag == "a" and self._in_genre_a:
            self._in_genre_a = False
            genre_text = self._genre_a_text.strip()
            genre_href = self._genre_a_href
            if genre_text:
                self.genres.append(genre_text)
                if "/serien/" in genre_href:
                    self.is_series = True

        if tag == "span" and self._in_genre_span:
            # The first span ends with the divider
            pass

        if tag == "span" and self._in_meta_span:
            self._in_meta_span = False
            text = self._meta_span_text.strip()
            if text:
                self._meta_spans.append(text)
                if re.match(r"^\d{4}$", text):
                    self.year = text
                elif "min" in text.lower():
                    self.duration = text
                elif "Staffel/Episode:" in text or "Staffel" in text:
                    self.is_series = True
                elif text.upper() in ("HD", "CAM", "TS", "SD", "4K", "HD/DEUTSCH"):
                    self.quality = text

        if tag == "div" and self._in_meta_line:
            if self._meta_line_div_depth > 0:
                self._meta_line_div_depth -= 1
            else:
                self._in_meta_line = False
                self._in_genre_span = False

        if tag == "div" and self._in_info:
            if self._info_div_depth > 0:
                self._info_div_depth -= 1
            else:
                self._in_info = False

        if tag == "a" and self._in_prose_a:
            self._in_prose_a = False

        if tag == "div" and self._in_prose:
            self._in_prose = False
            self.description = self._prose_text.strip()

        if tag == "div" and self._in_spoiler_title:
            self._in_spoiler_title = False
            title = self._spoiler_title_text.strip()
            # Extract season name: "Staffel 1"
            if title:
                self._spoiler_season = title

        if tag == "a" and self._in_episode_a:
            self._in_episode_a = False
            href = self._episode_a_href
            hoster = self._episode_a_text.strip()
            if href and hoster:
                # Build full URL if relative
                if href.startswith("/"):
                    href = urljoin(self._base_url, href)
                self.episode_links.append(
                    {
                        "hoster": hoster.split(".")[0].lower()
                        if "." in hoster
                        else hoster.lower(),
                        "link": href,
                        "season": self._spoiler_season,
                    }
                )

        if tag == "div" and self._in_spoiler_content:
            self._in_spoiler_content = False


def _parse_meinecloud_script(script_text: str) -> list[dict[str, str]]:
    """Parse meinecloud.click/ddl/{imdb_id} JS response.

    The script uses ``document.write()`` to inject HTML like::

        <a onclick="window.open('https://supervideo.cc/xxx')" class="streams">
          <span class="streaming">Supervideo</span>
          <mark>1080p</mark>
          <span style="color:#999;">1.0GB</span>
        </a>

    Returns list of dicts with keys: hoster, link, quality, size.
    """
    links: list[dict[str, str]] = []

    # Find all window.open('URL') patterns.
    # In document.write() strings, quotes are escaped as \' or \"
    for m in re.finditer(
        r"window\.open\(\s*\\?['\"]([^'\"\\]+)\\?['\"]\s*\)", script_text
    ):
        url = m.group(1)
        if not url.startswith("http"):
            continue

        # Extract hoster name, quality, size from following text.
        # Structure: window.open('URL')\" class=\"streams\">
        #   <span class=\"streaming\">HosterName</span>
        #   <mark>1080p</mark>
        #   <span style=\"color:#999;\">1.0GB</span></a>
        following = script_text[m.end() : m.end() + 500]

        hoster = ""
        hoster_m = re.search(r'class=\\"streaming\\"[^>]*>([^<]+)<', following)
        if hoster_m:
            hoster = hoster_m.group(1).strip()
        else:
            # Fallback: extract from URL domain
            domain_m = re.search(r"https?://([^/]+)", url)
            if domain_m:
                hoster = domain_m.group(1).split(".")[0]
        quality = ""
        quality_m = re.search(r"<mark[^>]*>([^<]+)<", following)
        if quality_m:
            quality = quality_m.group(1).strip()

        # Extract size from <span> with color:#999
        size = ""
        size_m = re.search(r"color:#999[^>]*>([^<]+)<", following)
        if size_m:
            size = size_m.group(1).strip()

        links.append(
            {
                "hoster": hoster.split(".")[0].lower()
                if "." in hoster
                else hoster.lower(),
                "link": url,
                "quality": quality,
                "size": size,
            }
        )

    return links


class HdfilmePlugin:
    """Python plugin for hdfilme.legal using httpx."""

    name = "hdfilme"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
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

    async def _search_page(self, query: str) -> list[dict[str, str]]:
        """Fetch search results page.

        Search uses GET with DLE CMS parameters:
        ``/?story={query}&do=search&subaction=search``

        Pagination is JS-based (only first page fetchable via httpx).
        Returns up to ~24 results per page.
        """
        client = await self._ensure_client()

        try:
            resp = await client.get(
                self.base_url,
                params={
                    "story": query,
                    "do": "search",
                    "subaction": "search",
                },
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("hdfilme_search_failed", query=query, error=str(exc))
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        log.info(
            "hdfilme_search_results",
            query=query,
            results=len(parser.results),
        )
        return parser.results

    async def _browse_page(
        self,
        path: str,
        page_num: int = 1,
    ) -> list[dict[str, str]]:
        """Fetch a browse/category listing page.

        Pages use ``/{path}/page/{n}/`` URL pattern.
        """
        client = await self._ensure_client()

        if page_num > 1:
            url = f"{self.base_url}/{path}/page/{page_num}/"
        else:
            url = f"{self.base_url}/{path}/"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "hdfilme_browse_failed",
                path=path,
                page=page_num,
                error=str(exc),
            )
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        log.info(
            "hdfilme_browse_page",
            path=path,
            page=page_num,
            count=len(parser.results),
        )
        return parser.results

    async def _browse_category(
        self,
        path: str,
    ) -> list[dict[str, str]]:
        """Browse a category with pagination up to _MAX_RESULTS items.

        Pages contain ~24 items each. 1000/24 ≈ 42 pages max.
        """
        max_pages = 42
        all_results: list[dict[str, str]] = []

        for page_num in range(1, max_pages + 1):
            results = await self._browse_page(path, page_num)
            if not results:
                break
            all_results.extend(results)
            if len(all_results) >= _MAX_RESULTS:
                break

        return all_results[:_MAX_RESULTS]

    async def _scrape_detail(self, result: dict[str, str]) -> list[SearchResult]:
        """Scrape a film/series detail page for stream links.

        For films: fetches meinecloud.click/ddl/{imdb_id} for stream URLs.
        For series: extracts episode links from su-spoiler-content divs.

        Returns one SearchResult for films, or one per first-episode-per-season
        for series (each with all episode links of that season as download_links).
        """
        client = await self._ensure_client()
        detail_url = result["url"]

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "hdfilme_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return []

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)

        title = parser.title or result.get("title", "")
        year = parser.year or result.get("year", "")
        genres = ", ".join(parser.genres) if parser.genres else ""

        # Determine category
        category = 5000 if parser.is_series else 2000
        # Genre-based override for films
        if not parser.is_series:
            for genre in parser.genres:
                key = genre.lower().strip()
                if key in _GENRE_CATEGORY_MAP:
                    category = _GENRE_CATEGORY_MAP[key]
                    break

        metadata = {
            "year": year,
            "genres": genres,
            "quality": parser.quality or result.get("quality", ""),
            "duration": parser.duration or result.get("duration", ""),
            "imdb_id": parser.imdb_id,
            "imdb_url": parser.imdb_url,
            "tmdb_url": parser.tmdb_url,
        }

        if parser.is_series:
            return self._build_series_results(
                title, parser, detail_url, category, metadata
            )

        return await self._build_film_results(
            title, parser, detail_url, category, metadata, client
        )

    async def _build_film_results(
        self,
        title: str,
        parser: _DetailPageParser,
        detail_url: str,
        category: int,
        metadata: dict[str, str],
        client: httpx.AsyncClient,
    ) -> list[SearchResult]:
        """Build SearchResult list for a film using meinecloud.click."""
        download_links: list[dict[str, str]] = []

        if parser.imdb_id:
            download_links = await self._fetch_meinecloud_links(parser.imdb_id, client)

        if not download_links:
            # No stream links found
            log.debug("hdfilme_no_streams", url=detail_url)
            return []

        description = (
            f"{metadata.get('genres', '')} ({metadata.get('year', '')})"
            if metadata.get("genres") and metadata.get("year")
            else metadata.get("genres") or metadata.get("year", "")
        )

        return [
            SearchResult(
                title=title,
                download_link=download_links[0]["link"],
                download_links=download_links,
                source_url=detail_url,
                category=category,
                description=description,
                metadata=metadata,
            )
        ]

    def _build_series_results(
        self,
        title: str,
        parser: _DetailPageParser,
        detail_url: str,
        category: int,
        metadata: dict[str, str],
    ) -> list[SearchResult]:
        """Build SearchResult list for a series from episode links."""
        if not parser.episode_links:
            log.debug("hdfilme_no_episode_links", url=detail_url)
            return []

        description = (
            f"{metadata.get('genres', '')} ({metadata.get('year', '')})"
            if metadata.get("genres") and metadata.get("year")
            else metadata.get("genres") or metadata.get("year", "")
        )

        # Return a single SearchResult with all episode links
        return [
            SearchResult(
                title=title,
                download_link=parser.episode_links[0]["link"],
                download_links=parser.episode_links,
                source_url=detail_url,
                category=category,
                description=description,
                metadata=metadata,
            )
        ]

    async def _fetch_meinecloud_links(
        self,
        imdb_id: str,
        client: httpx.AsyncClient,
    ) -> list[dict[str, str]]:
        """Fetch stream links from meinecloud.click/ddl/{imdb_id}."""
        url = f"{_MEINECLOUD_BASE}/ddl/{imdb_id}"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "hdfilme_meinecloud_failed",
                imdb_id=imdb_id,
                error=str(exc),
            )
            return []

        return _parse_meinecloud_script(resp.text)

    async def _scrape_all_details(
        self,
        items: list[dict[str, str]],
    ) -> list[SearchResult]:
        """Scrape detail pages with bounded concurrency."""
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)

        async def _bounded(r: dict[str, str]) -> list[SearchResult]:
            async with sem:
                return await self._scrape_detail(r)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in items],
            return_exceptions=True,
        )

        results: list[SearchResult] = []
        for item in gathered:
            if isinstance(item, list):
                for sr in item:
                    if isinstance(sr, SearchResult):
                        results.append(sr)
        return results

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search hdfilme.legal and return results with stream links."""
        await self._ensure_client()

        if query:
            all_items = await self._search_page(query)
        elif category and category in _CATEGORY_PATH_MAP:
            category_path = _CATEGORY_PATH_MAP[category]
            all_items = await self._browse_category(category_path)
        else:
            return []

        if not all_items:
            return []

        all_items = all_items[:_MAX_RESULTS]
        results = await self._scrape_all_details(all_items)

        # Filter by category if specified
        if category is not None:
            results = _filter_by_category(results, category)

        return results

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = HdfilmePlugin()
