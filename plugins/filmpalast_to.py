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
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.categories import (
    category_matches,
    served_category,
)
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

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


def _is_episode(title: str, season: int, episode: int | None) -> bool:
    """Whether *title* is an episode of *season* (and *episode*)."""
    m = _EPISODE_RE.search(title)
    if m is None or int(m.group(1)) != season:
        return False
    return episode is None or int(m.group(2)) == episode


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse filmpalast.to search results page.

    Each result has structure::

        <article>
          <h2><a href="/detail-url">Title</a></h2>
          ...
        </article>

    Extracts title and detail URL from each article. Every page but the
    last links the next one as ``<a class="pageing ...">vorwärts +</a>``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self.has_next_page = False
        self._in_pageing_a = False

        # State tracking
        self._in_article = False
        self._in_h2 = False
        self._in_a = False
        self._current_title = ""
        self._current_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self._in_article = True
            self._current_title = ""
            self._current_href = ""

        if tag == "h2" and self._in_article:
            self._in_h2 = True

        if tag == "a" and self._in_h2:
            attr_dict = dict(attrs)
            self._in_a = True
            self._current_href = attr_dict.get("href", "") or ""
            self._current_title = ""

        if tag == "a" and "pageing" in (dict(attrs).get("class") or ""):
            self._in_pageing_a = True

    def handle_data(self, data: str) -> None:
        if self._in_a and self._in_h2:
            self._current_title += data
        if self._in_pageing_a and data.strip().startswith("vorw"):
            self.has_next_page = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_pageing_a = False

        if tag == "a" and self._in_a:
            self._in_a = False

        if tag == "h2" and self._in_h2:
            self._in_h2 = False

        if tag == "article" and self._in_article:
            self._in_article = False
            title = self._current_title.strip()
            href = self._current_href.strip()
            if title and href:
                self.results.append({"title": title, "detail_url": href})


class _DetailPageParser(HTMLParser):
    """Parse filmpalast.to detail page for streaming links.

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

    Extracts title, release_name, and streaming links with hoster names.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title: str = ""
        self.release_name: str = ""
        self.links: list[dict[str, str]] = []

        # State tracking
        self._in_title_h2 = False
        self._in_release_span = False
        self._in_stream_list = False
        self._stream_div_depth = 0
        self._in_li = False
        self._in_hoster_p = False
        self._in_link_a = False
        self._current_hoster = ""
        self._current_link = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        # Title: <h2 class="bgDark">
        if tag == "h2" and "bgDark" in classes:
            self._in_title_h2 = True
            self.title = ""

        # Release name: <span id="release_text">
        if tag == "span" and attr_dict.get("id") == "release_text":
            self._in_release_span = True
            self.release_name = ""

        # Stream list container: <div id="grap-stream-list">
        if tag == "div":
            if attr_dict.get("id") == "grap-stream-list":
                self._in_stream_list = True
                self._stream_div_depth = 0
            elif self._in_stream_list:
                self._stream_div_depth += 1

        # List item in stream list
        if tag == "li" and self._in_stream_list:
            self._in_li = True
            self._current_hoster = ""
            self._current_link = ""

        # Hoster name: <p class="hostName"> or just <p> inside <li>
        if tag == "p" and self._in_li:
            if "hostName" in classes or not classes:
                self._in_hoster_p = True

        # Link: <a class="button iconPlay"> or <a class="button">
        if tag == "a" and self._in_li and "button" in classes:
            self._in_link_a = True
            # Try data-player-url first, then href, then onclick
            link = attr_dict.get("data-player-url", "")
            if not link:
                link = attr_dict.get("href", "") or ""
            if not link:
                onclick = attr_dict.get("onclick", "") or ""
                m = _ONCLICK_RE.search(onclick)
                if m:
                    link = m.group(1)
            self._current_link = link

    def handle_data(self, data: str) -> None:
        if self._in_title_h2:
            self.title += data
        if self._in_release_span:
            self.release_name += data
        if self._in_hoster_p:
            self._current_hoster += data

    def _handle_li_end(self) -> None:
        self._in_li = False
        hoster = self._current_hoster.strip()
        link = self._current_link.strip()
        # the site links some hosters to their login page instead of a video
        if link and not _ACCOUNT_PAGE_RE.search(link):
            self.links.append({"hoster": hoster or "unknown", "link": link})

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._in_title_h2:
            self._in_title_h2 = False
        elif tag == "span" and self._in_release_span:
            self._in_release_span = False
        elif tag == "p" and self._in_hoster_p:
            self._in_hoster_p = False
        elif tag == "a" and self._in_link_a:
            self._in_link_a = False
        elif tag == "li" and self._in_li:
            self._handle_li_end()
        elif tag == "div" and self._in_stream_list:
            if self._stream_div_depth > 0:
                self._stream_div_depth -= 1
            else:
                self._in_stream_list = False


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class FilmpalastPlugin(HttpxPluginBase):
    """Python plugin for filmpalast.to using httpx."""

    name = "filmpalast"
    provides = "stream"
    default_language = "de"
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

        parser = _SearchResultParser()
        parser.feed(resp.text)

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

    async def _scrape_detail(
        self, detail_url: str
    ) -> tuple[str, str, list[dict[str, str]]]:
        """Scrape a detail page for title, release_name, and links.

        Returns ``(title, release_name, links)`` where links is a list
        of ``{"hoster": ..., "link": ...}`` dicts.
        """
        resp = await self._safe_fetch(detail_url, context="detail_page")
        if resp is None:
            return "", "", []

        parser = _DetailPageParser()
        parser.feed(resp.text)
        return parser.title.strip(), parser.release_name.strip(), parser.links

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

        search_results = [
            item
            for item in await self._search_all(query)
            if category_matches(category, _item_category(item["title"]))
            and (season is None or _is_episode(item["title"], season, episode))
        ]
        if not search_results:
            return []

        sem = self._new_semaphore()

        async def _bounded_detail(item: dict[str, str]) -> SearchResult | None:
            async with sem:
                detail_url = urljoin(self.base_url, item["detail_url"])
                title, release_name, links = await self._scrape_detail(detail_url)
                if not links:
                    return None
                return SearchResult(
                    title=title or item["title"],
                    download_link=links[0]["link"],
                    download_links=links,
                    source_url=detail_url,
                    release_name=release_name or None,
                    category=_item_category(item["title"]),
                )

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
