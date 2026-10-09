"""filmpalast.to Python plugin for Scavengarr.

Scrapes filmpalast.to (German streaming site) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Two-stage scraping: search page -> detail page
- Search via GET /search/title/{query}
- Detail page: extract streaming links from grouped hoster lists
- Link attributes: data-player-url / href / onclick fallback chain
- Bounded concurrency for detail page scraping

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, urljoin

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.dom import classes, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["filmpalast.to"]
_MAX_PAGES = 32  # 32 results/page -> 32 pages for ~1000

# Regex to extract URL from onclick="window.open('url')" attributes
_ONCLICK_RE = re.compile(r"window\.open\(['\"]([^'\"]+)['\"]")
# Hoster account pages (e.g. https://vixeo.io/login) listed as "streams"
_ACCOUNT_PAGE_RE = re.compile(r"^https?://[^/]+/(?:login|register|signup)/?(?:$|\?)")
# The search term is one path segment: the site answers 404 to "/" (even
# encoded), "?" and "#" would end the path; its title search finds the
# titles without them
_PATH_BREAKERS_RE = re.compile(r"[/?#]")
# Series are listed per episode: "The Walking Dead: Dead City S03E08"
_EPISODE_RE = re.compile(r"\bS(\d{1,3})E(\d{1,4})\b", re.IGNORECASE)
# The labels of this site's results
_CATEGORIES = (2000, 5000)


def _item_category(title: str) -> int:
    """Films are 2000, episodes (``... S03E08``) 5000."""
    return 5000 if _EPISODE_RE.search(title) else 2000


def _series_title(item: dict[str, str]) -> str:
    """A hit's title without its episode tag ("Dark S01E01" -> "Dark")."""
    return _EPISODE_RE.sub("", hit_title(item)).strip()


def _is_episode(title: str, season: int, episode: int | None) -> bool:
    """Whether *title* is an episode of *season* (and *episode*)."""
    m = _EPISODE_RE.search(title)
    if m is None or int(m.group(1)) != season:
        return False
    return episode is None or int(m.group(2)) == episode


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------
class _SearchResultParser:
    """Parse filmpalast.to search results page (selectolax).

    Each result has structure::

        <article>
          <h2><a href="/detail-url">Title</a></h2>
          ...
        </article>

    Extracts title and detail URL from each article. Every page but the
    last links the next one as ``<a class="pageing ...">vorwärts +</a>``.
    """

    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []
        self.has_next_page = False

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for article in tree.css("article"):
            links = article.css("h2 a")
            if not links:
                continue
            title = links[-1].text().strip()
            href = (links[-1].attributes.get("href") or "").strip()
            if title and href:
                self.results.append({"title": title, "detail_url": href})
        self.has_next_page = self.has_next_page or any(
            a.text().strip().startswith("vorw") for a in tree.css("a.pageing")
        )


_PUBLISHED_RE = re.compile(r"Veröffentlicht:\s*((?:19|20)\d{2})")


class _DetailPageParser:
    """Parse filmpalast.to detail page for streaming links (selectolax).

    The detail pages are ~300 KB: html.parser took 20 ms per page on x86
    and the largest share of the parsing CPU on a Raspberry Pi.

    Structure::

        <h2 class="bgDark">Title</h2>
        <span id="release_text">Release.Name</span>
        <div id="grap-stream-list">
          <ul class="currentStreamLinks">
            <li>
              <p class="hostName">Voe</p>
              <a class="button iconPlay" data-player-url="https://...">Watch</a>
            </li>
            ...
          </ul>
        </div>

    Extracts title, release_name, and streaming links with hoster names
    (the last heading and release span of the page; per list item the
    text of its ``hostName`` or class-less paragraphs and its last
    ``button`` link), plus the year ("Veröffentlicht: 2023" in the
    "Shortinfos" entry of ``ul#detail-content-list``; an episode page shows
    its season's year) and the genres (the "Kategorien, Genre" entry).
    """

    def __init__(self) -> None:
        self.title: str = ""
        self.release_name: str = ""
        self.links: list[dict[str, str]] = []
        self.year: int | None = None
        self.genres: list[str] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        if titles := tree.css("h2.bgDark"):
            self.title = titles[-1].text()
        if names := tree.css("span#release_text"):
            self.release_name = names[-1].text()
        for info in tree.css("ul#detail-content-list li"):
            label = info.css_first("p")
            name = label.text().strip() if label is not None else ""
            if name == "Shortinfos":
                if m := _PUBLISHED_RE.search(info.text()):
                    self.year = int(m.group(1))
            elif name.startswith("Kategorien"):
                self.genres = [
                    text for a in info.css("span a") if (text := a.text().strip())
                ]
        for item in tree.css("div#grap-stream-list li"):
            buttons = item.css("a.button")
            link = _player_link(buttons[-1]).strip() if buttons else ""
            # the site links some hosters to their login page instead of a video
            if not link or _ACCOUNT_PAGE_RE.search(link):
                continue
            hoster = "".join(
                p.text()
                for p in item.css("p")
                if "hostName" in classes(p) or not classes(p)
            ).strip()
            self.links.append({"hoster": hoster or "unknown", "link": link})


def _player_link(button: LexborNode) -> str:
    """data-player-url, else href, else the URL of an onclick window.open()."""
    attrs = button.attributes
    link = attrs.get("data-player-url") or attrs.get("href") or ""
    if not link:
        m = _ONCLICK_RE.search(attrs.get("onclick") or "")
        if m:
            link = m.group(1)
    return link


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class FilmpalastPlugin(HttpxPluginBase):
    """Python plugin for filmpalast.to using httpx."""

    name = "filmpalast"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_page(
        self, term: str, page_num: int
    ) -> tuple[list[dict[str, str]], bool]:
        """Fetch one search results page.

        Returns ``({title, detail_url} list, has_next_page)``.
        """
        url = f"{self.base_url}/search/title/{quote(term, safe='')}"
        if page_num > 1:
            url += f"/{page_num}"
        resp = await self._safe_fetch(url, context="search_page")
        if resp is None:
            return [], False

        parser = await parse_page(_SearchResultParser(), resp.text)

        self._log.info(
            "filmpalast_search_page",
            query=term,
            page=page_num,
            count=len(parser.results),
        )
        return parser.results, parser.has_next_page

    async def _search_all(self, query: str) -> list[dict[str, str]]:
        """Follow the result pages up to the last one or ``_max_results``."""
        term = " ".join(_PATH_BREAKERS_RE.sub(" ", query).split())
        results: list[dict[str, str]] = []
        for page_num in range(1, _MAX_PAGES + 1):
            page_results, has_next_page = await self._search_page(term, page_num)
            results.extend(page_results)
            if not has_next_page or len(results) >= self.effective_max_results:
                break
        return results[: self.effective_max_results]

    async def _scrape_detail(self, detail_url: str) -> _DetailPageParser | None:
        """The parsed detail page (title, release name, links, year, genres);
        ``None`` when the page could not be fetched."""
        resp = await self._safe_fetch(detail_url, context="detail_page")
        if resp is None:
            return None
        return await parse_page(_DetailPageParser(), resp.text)

    async def _detail_result(self, item: dict[str, str]) -> SearchResult | None:
        """One search hit's result from its detail page; ``None`` without links."""
        detail_url = urljoin(self.base_url, item["detail_url"])
        detail = await self._scrape_detail(detail_url)
        if detail is None or not detail.links:
            return None
        category = _item_category(item["title"])
        metadata: dict[str, Any] = {"genres": ", ".join(detail.genres)}
        # An episode page shows its season's year, not the series' start
        # year (The Last of Us S02E01: 2025), so films only
        if detail.year is not None and category == 2000:
            metadata["year"] = detail.year
        return SearchResult(
            title=detail.title.strip() or item["title"],
            download_link=detail.links[0]["link"],
            download_links=detail.links,
            source_url=detail_url,
            release_name=detail.release_name.strip() or None,
            category=category,
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search filmpalast.to and return streaming results.

        Stage 1: Search pages for detail page URLs; the titles tell films
        from episodes, so category and season/episode filter here.
        Stage 2: Detail pages for streaming links (bounded concurrency).
        """
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            category = served_category(category, _CATEGORIES)
            if category is None:
                return []  # the site has films and series only

        await self._ensure_client()
        await self._verify_domain()

        hits = [
            item
            for item in await self._search_all(query)
            if category_matches(category, _item_category(item["title"]))
            and (season is None or _is_episode(item["title"], season, episode))
        ]
        # Each hit costs a 220 KB detail page: real matches only, compared
        # without the episode tag ("Dark Matter S01E01" is no "Dark")
        search_results = relevant_hits(
            hits,
            query,
            _series_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
        if not search_results:
            return []

        sem = self._new_semaphore()

        async def _bounded_detail(item: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._detail_result(item)

        raw = await asyncio.gather(
            *[_bounded_detail(item) for item in search_results],
            return_exceptions=True,
        )

        results: list[SearchResult] = []
        for r in raw:
            if isinstance(r, SearchResult):
                results.append(r)
            elif isinstance(r, Exception):
                self._log.warning("filmpalast_detail_error", error=str(r))

        return results


plugin = FilmpalastPlugin()
