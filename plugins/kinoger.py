"""kinoger.com Python plugin for Scavengarr.

Scrapes kinoger.com (German streaming site, DLE-based CMS) with:
- httpx requests via ``_fetch_text()``; the site sits behind a Cloudflare
  Turnstile challenge, so pages load through the browser fallback
  (``playwright.browser_fallback``)
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
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    STREAM_CATEGORIES,
    filter_by_category,
    served_category,
    stream_category,
)
from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page
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
_DOMAINS = ["kinoger.com", "kinoger.to"]
_MAX_PAGES = 84  # 12 results/page → 84 pages for ~1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Series badge pattern: S01, S01-04, S01E01-02, etc.
_SERIES_BADGE_RE = re.compile(r"S\d+", re.IGNORECASE)
# Episode list of a series tab:
#   <span onclick="pw.player('https://…', this);" data-id="1-5">
_EPISODE_PLAYER_RE = re.compile(r"""\.player\(\s*['"]\s*(https?://[^'"\s]+)""")
_EPISODE_ID_RE = re.compile(r"^(\d+)-(\d+)$")
# Year in the page title: "Oppenheimer (2023)"
_TITLE_YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)")
# JS player init of a tab without an iframe:
#   fsst.show(1,[['https://fsst.online/embed/905450/']],0.2)
#   ollhd.show(1,[['https://voe.sx/e/6qprs3ixu8el']],0.2)
_PLAYER_SHOW_RE = re.compile(r"""\.show\(\d+,\s*\[\[['"]?(https?://[^'"\]]+)""")
_IMDB_RATING_RE = re.compile(r"(\d+\.?\d*)")
# Badges that name a quality
_QUALITY_BADGES = frozenset(
    {"WEBRIP", "BDRIP", "CAMRIP", "TS", "HD", "SD", "4K", "HDTV"}
)


def _detect_series(badge: str, genres: list[str]) -> bool:
    """Detect if an item is a series based on badge text or genres."""
    if _SERIES_BADGE_RE.search(badge):
        return True
    lower_genres = [g.lower() for g in genres]
    return "serie" in lower_genres or "serien" in lower_genres


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


class _SearchResultParser:
    """Parse kinoger.com DLE search result page (selectolax).

    Each result is a pair of sibling divs::

        <div class="titlecontrol">
          <div class="title">
            <a href="https://kinoger.com/stream/1499-matrix-1999.html">
              Matrix (1999) Film
            </a>
          </div>
        </div>
        <div class="general_box">
          <div class="headerbar">
            <ul class="postinfo">
              <li class="category">
                <a href="...">Stream</a> / <a href="...">Sci-Fi</a>
              </li>
            </ul>
          </div>
          <div class="content_text searchresult_img">
            <b><div style="text-align:right;">DVDRip</div></b>
            ...
          </div>
        </div>

    The ``/stream/`` link in a ``titlecontrol``'s ``title`` div names a
    result; the next ``general_box`` holds its genres (the links of
    ``li.category``) and its quality (the first bold text of
    ``content_text``).
    """

    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str | list[str] | bool]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        title = url = ""
        tree = LexborHTMLParser(html)
        for block in tree.css("div:is(.titlecontrol, .general_box)"):
            if "titlecontrol" in classes(block):
                for link in block.css("div.title a"):
                    href = link.attributes.get("href") or ""
                    if "/stream/" in href:
                        url = urljoin(self._base_url, href)
                        title = link.text().strip()
            elif url:
                if title:
                    self._add_card(title, url, block)
                title = url = ""

    def _add_card(self, raw_title: str, url: str, box: LexborNode) -> None:
        # "Stream" is the site's section, not a genre
        genres = [
            text
            for link in box.css("li.category a")
            if (text := link.text().strip()) and text.lower() != "stream"
        ]
        bold = next(
            (text for b in box.css("div.content_text b") if (text := b.text().strip())),
            "",
        )
        # The bold text is a series badge (S01, S01-04) or a quality
        series_badge = bold if _SERIES_BADGE_RE.search(bold) else ""
        quality = "" if series_badge else bold
        # A " Serie" title suffix marks a series too (_clean_title strips it)
        is_series = _detect_series(series_badge, genres) or raw_title.endswith(
            (" Serie", " serie")
        )
        self.results.append(
            {
                "title": _clean_title(raw_title),
                "url": url,
                "genres": genres,
                "quality": quality,
                "badge": series_badge,
                "is_series": is_series,
            }
        )


class _DetailPageParser:
    """Parse kinoger.com detail page for stream tabs and metadata (selectolax).

    Stream tabs have structure::

        <div class="tabs">
          <label for="tab1" title="Stream HD+">Stream HD+</label>
          ...
          <section id="content1">
            <div id="container-video">
              <script>pw.show(1,[['https://fsst.online/embed/973704/']],0.2)</script>
              <ul id="kinog-serial" style="display: none;">
                <span onclick="pw.player('https://fsst.online/embed/973704/',
                      this);" data-id="1-1">1 Часть</span>
              </ul>
            </div>
          </section>
          <section id="content2">…kinoger.pw/e/<id>…</section>
        </div>

    A series lists its episodes visibly (``<ul id="kinog-serial">``,
    ``data-id="<season>-<episode>"``); a page with one player has no tabs
    and its ``container-video`` div stands alone.

    Metadata from the page body:
    - Title and year from the last ``h1`` (year: the first one with it)
    - Genres from breadcrumbs and the post info's category links
    - Description from the content area
    - IMDb rating if present
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.stream_links: list[dict[str, str]] = []
        self.episodes_listed = False
        self.title = ""
        self.year = ""
        self.genres: list[str] = []
        self.description = ""
        self.quality = ""
        self.imdb_rating = ""
        self.runtime = ""
        self.is_series = False
        self.badge = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        self._read_players(tree)
        self._read_metadata(tree)

    def _read_players(self, tree: LexborHTMLParser) -> None:
        # <label for="tab1" title="Stream HD+"> names <section id="content1">
        labels: dict[str, str] = {}
        for label in tree.css("label[for^='tab']"):
            text = label.attributes.get("title") or label.text().strip()
            if text:
                tab = label.attributes.get("for") or ""
                labels[f"content{tab.replace('tab', '')}"] = text
        # A page with one player has no tabs: its player container stands
        # alone and is read like a tab without a label
        for player in tree.css("section[id^='content'], div[id^='container-video']"):
            if player.tag == "section":
                self._add_player(
                    labels.get(player.attributes.get("id") or "", ""), player
                )
            elif not any(_is_player(parent) for parent in ancestors(player)):
                self._add_player("", player)

    def _add_player(self, label: str, player: LexborNode) -> None:
        """Links of a player tab: every episode of a series, else its stream.

        A series tab's player script starts at the first episode, so its
        URL alone would serve episode 1 for every request. A film's tab
        lists its stream as a hidden episode 1-1.
        """
        episodes: list[tuple[int, int, str]] = []
        for span in player.css("span[data-id]"):
            url = _EPISODE_PLAYER_RE.search(span.attributes.get("onclick") or "")
            number = _EPISODE_ID_RE.match(span.attributes.get("data-id") or "")
            if url and number:
                episodes.append(
                    (int(number.group(1)), int(number.group(2)), url.group(1))
                )
        # The player's episode list (kinog-serial, kinoger-serial, ...):
        # a film's player hides its single "1 Часть" entry
        lists = player.css("ul[id$='-serial']")
        style = (lists[-1].attributes.get("style") or "") if lists else ""
        film = "display:none" in style.replace(" ", "").lower()
        if episodes and not film:
            self.episodes_listed = True
            for season, episode, url in episodes:
                self.stream_links.append(
                    {
                        "hoster": _domain_from_url(url),
                        "link": url,
                        "label": episode_label(season, episode, label),
                    }
                )
            return
        url = _player_url(player) or next((url for _, _, url in episodes), "")
        if url:
            self.stream_links.append(
                {"hoster": _domain_from_url(url), "link": url, "label": label}
            )

    def _read_metadata(self, tree: LexborHTMLParser) -> None:
        for h1 in tree.css("h1"):
            text = h1.text()
            self.title = _clean_title(text)
            year = _TITLE_YEAR_RE.search(text)
            if year and not self.year:
                self.year = year.group(1)
        # Breadcrumbs, or the live theme's <li class="category"><a>Stream</a>
        # / <a>Drama</a></li>; "Stream" is the site's section
        self.genres = [
            text
            for node in tree.css("ul.breadcrumbs li, li.category a")
            if (text := node.text().strip()) and text.lower() != "stream"
        ]
        for span in tree.css("span.badge"):
            self._read_badge(span.text().strip())
        descriptions = [
            div
            for div in tree.css("div.full-text")
            if not any(
                parent.tag == "div" and "full-text" in classes(parent)
                for parent in ancestors(div)
            )
        ]
        if descriptions:
            self.description = descriptions[-1].text().strip()
        for span in tree.css("span.imdb"):
            rating = _IMDB_RATING_RE.search(span.text().strip())
            if rating:
                self.imdb_rating = rating.group(1)

    def _read_badge(self, text: str) -> None:
        if not text:
            return
        self.badge = text
        if _SERIES_BADGE_RE.search(text):
            self.is_series = True
        elif text.upper() in _QUALITY_BADGES:
            self.quality = text

    def finalize(self) -> None:
        """Post-processing: detect series from genres, extract year/runtime."""
        if self.episodes_listed:
            self.is_series = True
        lower_genres = [g.lower() for g in self.genres]
        if "serie" in lower_genres or "serien" in lower_genres:
            self.is_series = True

        # Try to extract year from title or description
        if not self.year:
            m = re.search(r"\b(19|20)\d{2}\b", self.description)
            if m:
                self.year = m.group(0)


def _is_player(node: LexborNode) -> bool:
    """Whether *node* is a player tab or a player container."""
    node_id = node.attributes.get("id") or ""
    if node.tag == "section":
        return node_id.startswith("content")
    return node.tag == "div" and node_id.startswith("container-video")


def _player_url(player: LexborNode) -> str:
    """The player's iframe (the last one), else the URL its script shows."""
    url = ""
    for iframe in player.css("iframe"):
        url = iframe.attributes.get("src") or url
    if url:
        return url
    for script in player.css("script"):
        show = _PLAYER_SHOW_RE.search(script.text())
        if show:
            return show.group(1)
    return ""


class KinogerPlugin(HttpxPluginBase):
    """Python plugin for kinoger.com using httpx."""

    name = "kinoger"
    provides = "stream"
    _domains = _DOMAINS

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
        params: dict[str, str] = {
            "do": "search",
            "subaction": "search",
            "story": query,
        }
        if page > 1:
            params["search_start"] = str(page)

        # Cloudflare Turnstile: _fetch_text falls back to the browser
        html = await self._fetch_text(
            f"{self.base_url}/index.php", params=params, context=f"search:{page}"
        )
        if html is None:
            return []

        parser = await parse_page(_SearchResultParser(self.base_url), html)

        self._log.info(
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
        """Fetch search results with pagination up to _max_results."""
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
        """Scrape a detail page for stream tabs and metadata.

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
            self._log.debug("kinoger_no_streams", url=detail_url)
            return None

        title = parser.title or str(result.get("title", ""))
        listed = result.get("genres")
        genres = parser.genres or (list(listed) if isinstance(listed, list) else [])
        is_series = parser.is_series or bool(result.get("is_series", False))
        quality = parser.quality or str(result.get("quality", ""))
        category = stream_category(genres, is_series=is_series)

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
        """Search kinoger.com and return results with stream links."""
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

        # Scrape detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(
            r: dict[str, str | list[str] | bool],
        ) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r, season, episode)

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


plugin = KinogerPlugin()
