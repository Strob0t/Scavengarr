"""movie2k.cx Python plugin for Scavengarr.

Scrapes movie2k.cx (German streaming site, AdonisJS backend) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- GET /search?q={query} for keyword search (no pagination)
- GET /movies?page={N} for browsing (20 results/page, up to 50 pages)
- GET /tv/all?page={N} for TV series browsing
- Detail page scraping for hoster URLs, IMDB, genres, year, runtime
- Category filtering (Movies/TV)
- Bounded concurrency for detail page scraping

Single domain: movie2k.cx (no active alternatives).
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["movie2k.cx"]
_MAX_PAGES = 50  # 20 results/page -> 50 pages for 1000 items (browse mode)
_RESULTS_PER_PAGE = 20

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

# Metadata regex patterns
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_RUNTIME_RE = re.compile(r"(\d+)\s*Min")
_COUNTRY_YEAR_RE = re.compile(r"Land/Jahr:\s*([^/]+)/(\d{4})")
_RATING_RE = re.compile(r"Bewertung:\s*([\d.]+)")
_IMDB_RE = re.compile(r"imdb\.com/title/(tt\d+)")


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


def _detect_category(genres: list[str], is_tv: bool) -> int:
    """Determine Torznab category from genres and TV flag."""
    if is_tv:
        lower_genres = [g.lower() for g in genres]
        if "anime" in lower_genres or "animation" in lower_genres:
            return 5070
        return 5000
    lower_genres = [g.lower() for g in genres]
    if "anime" in lower_genres or "animation" in lower_genres:
        return 5070
    return 2000


def _domain_from_url(url: str) -> str:
    """Extract domain name from a URL for hoster labeling."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.replace("www.", "").split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return parts[0] if parts and parts[0] else "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------------
# Search result parser (for /search?q= page)
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse movie2k.cx search results page.

    Each result is a separate <table> with structure::

        <table>
          <tr>
            <td><img src="tmdb.org/..." alt="Title"></td>
            <td>
              <h2><a href="/stream/{slug}">Title</a><img alt="Deutsch"></h2>
              ...
            </td>
          </tr>
        </table>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

        # State tracking
        self._in_h2 = False
        self._in_title_a = False
        self._current_title = ""
        self._current_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "h2":
            self._in_h2 = True
            self._current_title = ""
            self._current_url = ""

        if tag == "a" and self._in_h2:
            href = attr_dict.get("href", "") or ""
            if href and "/stream/" in href:
                self._current_url = urljoin(self._base_url, href)
                self._in_title_a = True
                self._current_title = ""

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title_a:
            self._in_title_a = False

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            title = self._current_title.strip()
            if title and self._current_url:
                # Deduplicate: same URL can appear multiple times
                if not any(r["url"] == self._current_url for r in self.results):
                    self.results.append(
                        {
                            "title": title,
                            "url": self._current_url,
                        }
                    )


# ---------------------------------------------------------------------------
# Browse result parser (for /movies?page= and /tv/all?page= pages)
# ---------------------------------------------------------------------------
class _BrowseResultParser(HTMLParser):
    """Parse movie2k.cx movies/TV listing page.

    Similar to search but with inline metadata::

        <h2><a href="/stream/{slug}">Title</a><img alt="Deutsch"></h2>
        <div>
          Genre: <a href="/movies/Action">Action</a>, ...
          | Bewertung: 6.3 | 2025 | 100 Min
          <a href="#">Info</a>
        </div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str | list[str]]] = []
        self._base_url = base_url

        # State tracking
        self._in_h2 = False
        self._in_title_a = False
        self._current_title = ""
        self._current_url = ""

        # Metadata div after h2
        self._in_meta_div = False
        self._meta_div_depth = 0
        self._meta_text = ""
        self._genres: list[str] = []
        self._in_genre_a = False
        self._genre_text = ""
        self._expecting_meta = False

    def _emit_result(self) -> None:
        if not self._current_title or not self._current_url:
            self._reset()
            return

        # Parse metadata from accumulated text
        year = ""
        rating = ""
        runtime = ""

        m = _YEAR_RE.search(self._meta_text)
        if m:
            year = m.group(0)
        m = _RATING_RE.search(self._meta_text)
        if m:
            rating = m.group(1)
        m = _RUNTIME_RE.search(self._meta_text)
        if m:
            runtime = m.group(1)

        # Deduplicate
        if not any(r["url"] == self._current_url for r in self.results):
            self.results.append(
                {
                    "title": self._current_title,
                    "url": self._current_url,
                    "genres": list(self._genres),
                    "year": year,
                    "rating": rating,
                    "runtime": runtime,
                }
            )
        self._reset()

    def _reset(self) -> None:
        self._current_title = ""
        self._current_url = ""
        self._meta_text = ""
        self._genres = []
        self._expecting_meta = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)

        if tag == "h2":
            # New result starts: emit previous if pending
            if self._expecting_meta and self._current_url:
                self._emit_result()
            self._in_h2 = True
            self._current_title = ""
            self._current_url = ""

        if tag == "a" and self._in_h2:
            href = attr_dict.get("href", "") or ""
            if href and "/stream/" in href:
                self._current_url = urljoin(self._base_url, href)
                self._in_title_a = True
                self._current_title = ""

        # Meta div follows h2 (first div after h2 end)
        if tag == "div" and self._expecting_meta and not self._in_meta_div:
            self._in_meta_div = True
            self._meta_div_depth = 0
            self._meta_text = ""
            self._genres = []
        elif tag == "div" and self._in_meta_div:
            self._meta_div_depth += 1

        # Genre links inside meta div
        if tag == "a" and self._in_meta_div:
            href = attr_dict.get("href", "") or ""
            if "/movies/" in href or "/tv/" in href:
                self._in_genre_a = True
                self._genre_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_title_a:
            self._current_title += data
        if self._in_genre_a:
            self._genre_text += data
        if self._in_meta_div:
            self._meta_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._in_title_a:
                self._in_title_a = False
            if self._in_genre_a:
                self._in_genre_a = False
                text = self._genre_text.strip()
                if text:
                    self._genres.append(text)

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            self._current_title = self._current_title.strip()
            if self._current_url:
                self._expecting_meta = True

        if tag == "div" and self._in_meta_div:
            if self._meta_div_depth > 0:
                self._meta_div_depth -= 1
            else:
                self._in_meta_div = False
                self._expecting_meta = False
                # Emit the result now that metadata is collected
                if self._current_url:
                    self._emit_result()

    def finalize(self) -> None:
        """Emit any remaining pending result."""
        if self._current_url and self._current_title:
            self._emit_result()


# ---------------------------------------------------------------------------
# Detail page parser (for /stream/{slug} page)
# ---------------------------------------------------------------------------
class _DetailPageParser(HTMLParser):
    """Parse movie2k.cx detail/stream page.

    Stream links::

        <div id="tablemoviesindex2">
          <a href="https://voe.sx/ssbkh7j0ksb6">
            17-12-25 17:28 <img> voe.sx
            <div>Qualität: <img alt="HD-1080p"></div>
          </a>
        </div>

    Metadata::

        <h1>Title <img> Qualität: <img alt="HD"></h1>
        <div>Genre: <a href="/movies/Action">Action</a>, ...</div>
        <div>IMDB Bewertung: <a href="imdb.com/title/tt...">6.93</a>
             | ... | Länge: 140 Minuten | Land/Jahr: USA/2013</div>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Stream links
        self.stream_links: list[dict[str, str]] = []
        self._in_stream_div = False
        self._stream_div_depth = 0
        self._in_stream_a = False
        self._stream_a_href = ""
        self._stream_quality = ""

        # Title
        self.title = ""
        self._in_h1 = False
        self._h1_text = ""

        # Genres
        self.genres: list[str] = []
        self._in_genre_a = False
        self._genre_text = ""

        # IMDB
        self.imdb_url = ""
        self.imdb_rating = ""
        self._in_imdb_a = False
        self._imdb_text = ""

        # Metadata text (accumulated from divs)
        self.year = ""
        self.runtime = ""
        self.country = ""
        self.description = ""
        self._meta_texts: list[str] = []

        # Description tracking — the long div with movie summary
        self._seen_h1 = False
        self._in_desc_candidate = False
        self._desc_depth = 0
        self._desc_text = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        tag_id = attr_dict.get("id", "") or ""
        href = attr_dict.get("href", "") or ""
        alt = attr_dict.get("alt", "") or ""

        # Stream link container: <div id="tablemoviesindex2">
        if tag == "div" and tag_id == "tablemoviesindex2":
            self._in_stream_div = True
            self._stream_div_depth = 0
        elif tag == "div" and self._in_stream_div:
            self._stream_div_depth += 1

        # Stream link: <a href="https://voe.sx/..."> inside stream div
        if tag == "a" and self._in_stream_div:
            if href.startswith("http") and "movie2k" not in href:
                self._in_stream_a = True
                self._stream_a_href = href
                self._stream_quality = ""

        # Quality image inside stream link: <img alt="HD-1080p">
        if tag == "img" and self._in_stream_a:
            if alt and ("HD" in alt or "SD" in alt or "CAM" in alt):
                self._stream_quality = alt

        # Quality image in h1: <img alt="HD">
        if tag == "img" and self._in_h1:
            pass  # Ignore quality img in title

        # Title: <h1>
        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # Genre links: <a href="/movies/{Genre}">
        if tag == "a" and "/movies/" in href and not self._in_stream_div:
            self._in_genre_a = True
            self._genre_text = ""

        # IMDB link: <a href="https://www.imdb.com/title/...">
        if tag == "a" and "imdb.com" in href:
            self._in_imdb_a = True
            self._imdb_text = ""
            self.imdb_url = href

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_genre_a:
            self._genre_text += data
        if self._in_imdb_a:
            self._imdb_text += data

        # Collect all visible text for metadata extraction
        text = data.strip()
        if text and len(text) > 10:
            self._meta_texts.append(text)

    def _end_a_tag(self) -> None:
        """Handle closing of ``<a>`` tag for streams, genres, IMDB."""
        if self._in_stream_a:
            self._in_stream_a = False
            if self._stream_a_href:
                domain = _domain_from_url(self._stream_a_href)
                self.stream_links.append(
                    {
                        "hoster": domain,
                        "link": self._stream_a_href,
                        "quality": self._stream_quality or "HD",
                    }
                )
            self._stream_a_href = ""
            self._stream_quality = ""

        if self._in_genre_a:
            self._in_genre_a = False
            text = self._genre_text.strip()
            if text and text not in self.genres:
                self.genres.append(text)

        if self._in_imdb_a:
            self._in_imdb_a = False
            text = self._imdb_text.strip()
            m = re.search(r"([\d.]+)", text)
            if m:
                self.imdb_rating = m.group(1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._end_a_tag()

        if tag == "div" and self._in_stream_div:
            if self._stream_div_depth > 0:
                self._stream_div_depth -= 1
            else:
                self._in_stream_div = False

        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self._seen_h1 = True
            # Clean title: remove "Qualität:" suffix and whitespace
            title = self._h1_text.strip()
            title = re.sub(r"\s*Qualität:.*$", "", title).strip()
            self.title = title

    def finalize(self) -> None:
        """Post-processing: extract year, runtime, country from collected text."""
        full_text = " ".join(self._meta_texts)

        # Extract year from "Land/Jahr: USA/2013" or standalone 4-digit year
        m = _COUNTRY_YEAR_RE.search(full_text)
        if m:
            self.country = m.group(1).strip()
            self.year = m.group(2)
        elif not self.year:
            m = _YEAR_RE.search(full_text)
            if m:
                self.year = m.group(0)

        # Extract runtime from "Länge: 140 Minuten"
        m = re.search(r"Länge:\s*(\d+)\s*Minuten", full_text)
        if m:
            self.runtime = m.group(1)

        # Extract description (longest text block)
        if self._meta_texts:
            longest = max(self._meta_texts, key=len)
            if len(longest) > 50:
                self.description = longest.strip()

        # Deduplicate genres
        seen: set[str] = set()
        unique: list[str] = []
        for g in self.genres:
            if g not in seen:
                seen.add(g)
                unique.append(g)
        self.genres = unique


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class Movie2kPlugin(HttpxPluginBase):
    """Python plugin for movie2k.cx using httpx."""

    name = "movie2k"
    provides = "stream"
    default_language = "de"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
    ) -> list[dict[str, str]]:
        """Fetch search results page (no pagination on search)."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/search",
                params={"q": query},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "movie2k_search_failed",
                query=query,
                error=str(exc),
            )
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(resp.text)

        self._log.info(
            "movie2k_search_page",
            query=query,
            results=len(parser.results),
        )
        return parser.results

    async def _browse_pages(
        self,
        path: str,
        max_pages: int | None = None,
    ) -> list[dict[str, str | list[str]]]:
        """Fetch listing pages with pagination (for empty query browse).

        Args:
            path: URL path, e.g. "/movies" or "/tv/all".
            max_pages: Maximum pages to fetch.
        """
        client = await self._ensure_client()
        all_results: list[dict[str, str | list[str]]] = []
        pages = max_pages or _MAX_PAGES

        for page_num in range(1, pages + 1):
            params: dict[str, str] = {
                "page": str(page_num),
                "sort_by": "createdAt",
                "order": "desc",
            }

            try:
                resp = await client.get(
                    f"{self.base_url}{path}",
                    params=params,
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "movie2k_browse_failed",
                    path=path,
                    page=page_num,
                    error=str(exc),
                )
                break

            parser = _BrowseResultParser(self.base_url)
            parser.feed(resp.text)
            parser.finalize()

            if not parser.results:
                break

            all_results.extend(parser.results)
            self._log.info(
                "movie2k_browse_page",
                path=path,
                page=page_num,
                results=len(parser.results),
                total=len(all_results),
            )

            if len(all_results) >= self._max_results:
                break

        return all_results[: self._max_results]

    async def _scrape_detail(
        self,
        result: dict[str, str | list[str]],
    ) -> SearchResult | None:
        """Scrape a detail page for hoster URLs and metadata."""
        client = await self._ensure_client()
        detail_url = str(result["url"])

        try:
            resp = await client.get(detail_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "movie2k_detail_failed",
                url=detail_url,
                error=str(exc),
            )
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(resp.text)
        parser.finalize()

        if not parser.stream_links:
            self._log.debug("movie2k_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        is_tv = "type=tv" in detail_url
        year = parser.year or str(result.get("year", ""))
        category = _detect_category(genres, is_tv)

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
            "quality": parser.stream_links[0].get("quality", ""),
            "imdb_rating": parser.imdb_rating,
            "imdb_url": parser.imdb_url,
            "runtime": parser.runtime,
            "country": parser.country,
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
        """Search movie2k.cx and return results with stream links."""
        await self._ensure_client()
        await self._verify_domain()

        # Determine if we should browse TV
        is_tv_request = (
            category is not None and category in _TV_CATEGORIES
        ) or season is not None

        # Get initial results
        if query:
            items = await self._search_page(query)
        elif is_tv_request:
            items = await self._browse_pages("/tv/all")
        else:
            items = await self._browse_pages("/movies")

        if not items:
            return []

        # Scrape detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(
            r: dict[str, str | list[str]],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        # When season is requested, restrict to series results
        effective_category = category
        if season is not None and effective_category is None:
            effective_category = 5000

        if effective_category is not None:
            results = _filter_by_category(results, effective_category)

        return results


plugin = Movie2kPlugin()
