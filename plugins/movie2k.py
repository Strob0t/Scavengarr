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
import base64
import re
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    STREAM_CATEGORIES,
    filter_by_category,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, parse_page
from scavengarr.infrastructure.plugins.episodes import episode_label, filter_episodes
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["movie2k.cx"]
_MAX_PAGES = 50  # 20 results/page -> 50 pages for 1000 items (browse mode)
_RESULTS_PER_PAGE = 20

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Metadata regex patterns
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_RUNTIME_RE = re.compile(r"(\d+)\s*Min")
_COUNTRY_YEAR_RE = re.compile(r"Land/Jahr:\s*([^/]+)/(\d{4})")
_RATING_RE = re.compile(r"Bewertung:\s*([\d.]+)")
_IMDB_RE = re.compile(r"imdb\.com/title/(tt\d+)")
# Mirror link of a stream entry: onclick="return loadMirror('<url>')"
# (series pages set href="#", films repeat the URL in href)
_LOAD_MIRROR_RE = re.compile(r"loadMirror\(\s*'([^']+)'")
# Decoded data-episode-id of a series page: "tt3581920-s1e1-1"
_EPISODE_ID_RE = re.compile(r"-s(\d+)e(\d+)(?:-|$)")


def _episode_from_id(value: str) -> tuple[int, int] | None:
    """(season, episode) of a series page's base64 ``data-episode-id``."""
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    m = _EPISODE_ID_RE.search(decoded)
    return (int(m.group(1)), int(m.group(2))) if m else None


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
class _SearchResultParser:
    """Parse movie2k.cx search results page (selectolax).

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
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        for heading in LexborHTMLParser(html).css("h2"):
            title, url = _title_link(heading, self._base_url)
            # Deduplicate: same URL can appear multiple times
            if title and url and not any(r["url"] == url for r in self.results):
                self.results.append({"title": title, "url": url})


def _title_link(heading: LexborNode, base_url: str) -> tuple[str, str]:
    """Title and URL of a result's ``h2``: its last ``/stream/`` link."""
    links = heading.css("a[href*='/stream/']")
    if not links:
        return "", ""
    href = links[-1].attributes.get("href") or ""
    return links[-1].text().strip(), urljoin(base_url, href)


# ---------------------------------------------------------------------------
# Browse result parser (for /movies?page= and /tv/all?page= pages)
# ---------------------------------------------------------------------------
class _BrowseResultParser:
    """Parse movie2k.cx movies/TV listing page (selectolax).

    Similar to search but with inline metadata::

        <h2><a href="/stream/{slug}">Title</a><img alt="Deutsch"></h2>
        <div>
          Genre: <a href="/movies/Action">Action</a>, ...
          | Bewertung: 6.3 | 2025 | 100 Min
          <a href="#">Info</a>
        </div>

    The first div after a result's ``h2`` (before the next ``h2``) holds its
    metadata. A result without one is kept without metadata: at the next
    ``h2``, or in ``finalize()`` for the page's last one.
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str | list[str]]] = []
        self._base_url = base_url
        # (title, url) of the last h2 while its metadata div has not come
        self._pending: tuple[str, str] | None = None

    def feed(self, html: str) -> None:
        heading = 0  # mem_id of the last h2
        for node in LexborHTMLParser(html).css("h2, div"):
            if node.tag == "h2":
                # New result starts: emit previous if pending
                self.finalize()
                heading = node.mem_id
                title, url = _title_link(node, self._base_url)
                self._pending = (title, url) if url else None
            elif self._pending and all(p.mem_id != heading for p in ancestors(node)):
                # Meta div follows h2 (a div inside the h2 starts before its end)
                self._emit_result(*self._pending, node)
                self._pending = None

    def _emit_result(self, title: str, url: str, meta: LexborNode | None) -> None:
        if not title:
            return

        # Metadata text and genre links of the meta div
        text = ""
        genres: list[str] = []
        if meta is not None:
            text = meta.text()
            genres = [
                genre
                for link in meta.css("a:is([href*='/movies/'], [href*='/tv/'])")
                if (genre := link.text().strip())
            ]

        # Parse metadata from the text
        year = ""
        rating = ""
        runtime = ""

        m = _YEAR_RE.search(text)
        if m:
            year = m.group(0)
        m = _RATING_RE.search(text)
        if m:
            rating = m.group(1)
        m = _RUNTIME_RE.search(text)
        if m:
            runtime = m.group(1)

        # Deduplicate
        if not any(r["url"] == url for r in self.results):
            self.results.append(
                {
                    "title": title,
                    "url": url,
                    "genres": genres,
                    "year": year,
                    "rating": rating,
                    "runtime": runtime,
                }
            )

    def finalize(self) -> None:
        """Emit any remaining pending result."""
        if self._pending:
            self._emit_result(*self._pending, None)
            self._pending = None


# ---------------------------------------------------------------------------
# Detail page parser (for /stream/{slug} page)
# ---------------------------------------------------------------------------
class _DetailPageParser:
    """Parse movie2k.cx detail/stream page (selectolax).

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
        self._base_url = base_url

        # Stream links; a series page lists every episode in its own
        # <table data-episode-id="…">
        self.stream_links: list[dict[str, str]] = []
        self.episodes_listed = False

        # Title
        self.title = ""

        # Genres
        self.genres: list[str] = []

        # IMDB
        self.imdb_url = ""
        self.imdb_rating = ""

        # Metadata text (the page's text blocks)
        self.year = ""
        self.runtime = ""
        self.country = ""
        self.description = ""
        self._meta_texts: list[str] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        # Inline scripts and styles are no page text (the description)
        tree.strip_tags(["script", "style"])

        # Stream link container: <div id="tablemoviesindex2">
        for link in tree.css("div[id='tablemoviesindex2'] a"):
            self._add_stream_link(link)

        # Title: <h1> (the last one)
        for h1 in tree.css("h1"):
            # Clean title: remove "Qualität:" suffix and whitespace
            title = h1.text().strip()
            self.title = re.sub(r"\s*Qualität:.*$", "", title).strip()

        # Genre links: <a href="/movies/{Genre}"> outside the stream links
        for link in tree.css("a[href*='/movies/']"):
            text = link.text().strip()
            if text and text not in self.genres and not _in_stream_div(link):
                self.genres.append(text)

        # IMDB link: <a href="https://www.imdb.com/title/..."> (the last one)
        for link in tree.css("a[href*='imdb.com']"):
            self.imdb_url = link.attributes.get("href") or ""
            m = re.search(r"([\d.]+)", link.text().strip())
            if m:
                self.imdb_rating = m.group(1)

        # Collect all visible text for metadata extraction, a block per text
        # node (elements have no text_content)
        if tree.root is not None:
            for node in tree.root.traverse(include_text=True, skip_empty=True):
                text = (node.text_content or "").strip()
                if len(text) > 10:
                    self._meta_texts.append(text)

    def _add_stream_link(self, link: LexborNode) -> None:
        """Add a link of a stream div unless it stays on the site.

        <a href="https://voe.sx/..."> (films) or
        <a href="#" onclick="return loadMirror('https://...')"> (series)
        """
        href = link.attributes.get("href") or ""
        mirror = _LOAD_MIRROR_RE.search(link.attributes.get("onclick") or "")
        url = href if href.startswith("http") else (mirror.group(1) if mirror else "")
        if not url.startswith("http") or "movie2k" in url:
            return

        # Quality image inside stream link: <img alt="HD-1080p"> (the last one)
        quality = ""
        for img in link.css("img"):
            alt = img.attributes.get("alt") or ""
            if "HD" in alt or "SD" in alt or "CAM" in alt:
                quality = alt

        domain = _domain_from_url(url)
        stream = {"hoster": domain, "link": url, "quality": quality or "HD"}
        episode = _episode_of(link)
        if episode is not None:
            self.episodes_listed = True
            stream["label"] = episode_label(*episode, domain)
        self.stream_links.append(stream)

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


def _in_stream_div(node: LexborNode) -> bool:
    """Whether *node* is inside a ``<div id="tablemoviesindex2">``."""
    return any(
        parent.tag == "div" and parent.attributes.get("id") == "tablemoviesindex2"
        for parent in ancestors(node)
    )


def _episode_of(link: LexborNode) -> tuple[int, int] | None:
    """(season, episode) of a series page's stream link.

    The link's outermost ``<table data-episode-id="…">`` names it; tables
    nested in that one are part of the episode.
    """
    episode = None
    for parent in ancestors(link):
        if parent.tag == "table":
            found = _episode_from_id(parent.attributes.get("data-episode-id") or "")
            if found is not None:
                episode = found
    return episode


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class Movie2kPlugin(HttpxPluginBase):
    """Python plugin for movie2k.cx using httpx."""

    name = "movie2k"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self,
        query: str,
    ) -> list[dict[str, str]]:
        """Fetch search results page (no pagination on search)."""
        html = await self._fetch_text(
            f"{self.base_url}/search", params={"q": query}, context="search"
        )
        if html is None:
            return []

        parser = await parse_page(_SearchResultParser(self.base_url), html)

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
        all_results: list[dict[str, str | list[str]]] = []
        pages = max_pages or _MAX_PAGES

        for page_num in range(1, pages + 1):
            params: dict[str, str] = {
                "page": str(page_num),
                "sort_by": "createdAt",
                "order": "desc",
            }

            html = await self._fetch_text(
                f"{self.base_url}{path}", params=params, context="browse"
            )
            if html is None:
                break

            parser = await parse_page(_BrowseResultParser(self.base_url), html)
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

            if len(all_results) >= self.effective_max_results:
                break

        return all_results[: self.effective_max_results]

    async def _scrape_detail(
        self,
        result: dict[str, str | list[str]],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Scrape a detail page for hoster URLs and metadata.

        A series page lists every episode: a season/episode request keeps
        the links of that episode.
        """
        detail_url = str(result["url"])
        html = await self._fetch_text(detail_url, context="detail")
        if html is None:
            return None

        parser = await parse_page(_DetailPageParser(self.base_url), html)
        parser.finalize()

        links = parser.stream_links
        if season is not None and parser.episodes_listed:
            links = filter_episodes(links, season, episode)
        if not links:
            self._log.debug("movie2k_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        genres = parser.genres or list(result.get("genres", []))
        is_tv = parser.episodes_listed or "type=series" in detail_url
        year = parser.year or str(result.get("year", ""))
        category = stream_category(genres, is_series=is_tv)

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
            "quality": links[0].get("quality", ""),
            "imdb_rating": parser.imdb_rating,
            "imdb_url": parser.imdb_url,
            "runtime": parser.runtime,
            "country": parser.country,
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
        """Search movie2k.cx and return results with stream links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, STREAM_CATEGORIES)
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()
        await self._verify_domain()

        # Determine if we should browse TV
        is_tv_request = category is not None and category >= 5000

        # Get initial results
        items: list[dict[str, str | list[str]]]
        if query:
            # Each hit costs a detail page: scrape real matches only
            items = relevant_hits(
                [dict(r) for r in await self._search_page(query)],
                query,
                hit_title,
                limit=SINGLE_TITLE_HITS if season is not None else None,
            )
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
                return await self._scrape_detail(r, season, episode)

        gathered = await asyncio.gather(
            *[_bounded(r) for r in items],
            return_exceptions=True,
        )

        results: list[SearchResult] = [
            r for r in gathered if isinstance(r, SearchResult)
        ]

        if category is not None:
            results = filter_by_category(results, category)

        return results


plugin = Movie2kPlugin()
