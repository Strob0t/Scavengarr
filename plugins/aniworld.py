"""aniworld.to Python plugin for Scavengarr.

Scrapes aniworld.to (German anime streaming site) with:
- httpx for all requests (server-rendered HTML, AJAX search API)
- POST /ajax/search with keyword={query} -> JSON array of matches
- Detail page scraping for metadata (description, genres, cover image)
- Episode page scraping for hoster redirect links (VOE, Filemoon, etc.)
- Category: always 5070 (Anime) since site is anime-only
- Bounded concurrency for detail page scraping

Domain: aniworld.to (aniworld.info is a scam copy with ad pages).
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.dom import parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
# aniworld.info is a scam copy with ad pages (JDownloader SerienStreamTo)
_DOMAINS = ["aniworld.to"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A series' page; the ajax search also links FAQ pages and episodes
_SERIES_LINK_RE = re.compile(r"^/anime/stream/[^/]+/?$")

# Language key mapping from aniworld.to data-lang-key attributes.
_LANG_MAP: dict[str, str] = {
    "1": "German Dub",
    "2": "English Sub",
    "3": "German Sub",
}


class _DetailPageParser:
    """Parse aniworld.to anime detail page (selectolax).

    Extracts:
    - Description from ``.seri_des`` (via ``data-full-description``; a
      ``<p>`` in the current theme, a ``<div>`` before)
    - Genres from ``.genres ul li a`` elements
    - Cover image URL from the first ``img[data-src]`` (``.seriesCoverBox``)
    - First episode URL from ``table.seasonEpisodesList tbody tr td a``
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

        self.description = ""
        self.genres: list[str] = []
        self.cover_url = ""
        self.first_episode_url = ""

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for node in tree.css(".seri_des"):
            # A full description wins (the last one); else the first
            # non-empty text of a .seri_des element
            full_desc = node.attributes.get("data-full-description") or ""
            if full_desc:
                self.description = full_desc.strip()
            if not self.description:
                self.description = node.text().strip()
        for link in tree.css("div.genres ul li a"):
            genre = link.text().strip()
            if genre:
                self.genres.append(genre)
        for img in tree.css("img[data-src]"):
            data_src = img.attributes.get("data-src") or ""
            if data_src:
                self.cover_url = urljoin(self._base_url, data_src)
                break
        for link in tree.css("table.seasonEpisodesList tbody tr a[href]"):
            href = link.attributes.get("href") or ""
            if "/staffel-" in href and "/episode-" in href:
                self.first_episode_url = urljoin(self._base_url, href)
                break


class _EpisodePageParser:
    """Parse aniworld.to episode page for hoster links (selectolax).

    Extracts hoster redirect links from::

        <li data-lang-key="1" data-link-id="123"
            data-link-target="/redirect/123">
          <div class="watchEpisode">
            <a ...><h4>VOE</h4></a>
          </div>
        </li>
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self.hoster_links: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for li in tree.css("li[data-lang-key][data-link-target]"):
            lang_key = li.attributes.get("data-lang-key") or ""
            redirect = li.attributes.get("data-link-target") or ""
            if not lang_key or not redirect:
                continue
            # The <h4> names the hoster
            for h4 in li.css("h4"):
                hoster_name = h4.text().strip()
                if hoster_name:
                    self.hoster_links.append(
                        {
                            "hoster": hoster_name.lower(),
                            "link": urljoin(self._base_url, redirect),
                            "language": _LANG_MAP.get(lang_key, lang_key),
                        }
                    )


class AniworldPlugin(HttpxPluginBase):
    """Python plugin for aniworld.to using httpx."""

    name = "aniworld"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"

    _domains = _DOMAINS

    async def _ajax_search(self, query: str) -> list[dict[str, str]]:
        """Search via POST /ajax/search endpoint.

        Returns JSON array of {title, description, link} dicts.
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/ajax/search",
            method="POST",
            context="ajax_search",
            data={"keyword": query},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if resp is None or not resp.text.strip():
            # A search without hits is answered with an empty body
            return []

        data = self._safe_parse_json(resp, context="ajax_search")
        if not isinstance(data, list):
            return []

        results: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            link = item.get("link", "")
            description = item.get("description", "")
            # The search also lists FAQ pages and episodes
            if title and _SERIES_LINK_RE.match(link):
                results.append(
                    {
                        "title": _strip_html_tags(title),
                        "link": link,
                        "description": _strip_html_tags(description),
                    }
                )

        self._log.info(
            "aniworld_search_results",
            query=query,
            results=len(results),
        )
        return results[: self.effective_max_results]

    async def _scrape_detail(
        self,
        item: dict[str, str],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Scrape anime detail page and episode for hoster links.

        When *season* is given the plugin navigates directly to
        ``/anime/stream/{slug}/staffel-{season}/episode-{episode or 1}``
        instead of scraping the first episode on the detail page.
        """
        detail_url = item["link"]
        if not detail_url.startswith("http"):
            detail_url = urljoin(self.base_url, detail_url)

        # Fetch detail page
        resp = await self._safe_fetch(detail_url, context="detail_page")
        if resp is None:
            return None

        detail_parser = await parse_page(_DetailPageParser(self.base_url), resp.text)

        # Determine which episode page to scrape
        hoster_links: list[dict[str, str]] = []
        if season is not None:
            # Episode pages live below the detail page
            # (/anime/stream/<slug>/staffel-N/episode-M); a season without
            # episode starts at that season's first episode
            ep_url = f"{detail_url.rstrip('/')}/staffel-{season}/episode-{episode or 1}"
            hoster_links = await self._scrape_episode(ep_url)
        elif detail_parser.first_episode_url:
            hoster_links = await self._scrape_episode(detail_parser.first_episode_url)

        if not hoster_links:
            self._log.debug("aniworld_no_hosters", url=detail_url)
            return None

        title = item.get("title", "")
        genres = ", ".join(detail_parser.genres) if detail_parser.genres else ""
        description = detail_parser.description or item.get("description", "")

        metadata: dict[str, str] = {
            "genres": genres,
            "cover_url": detail_parser.cover_url,
        }

        return SearchResult(
            title=title,
            download_link=hoster_links[0]["link"],
            download_links=hoster_links,
            source_url=detail_url,
            category=5070,
            description=description,
            metadata=metadata,
        )

    async def _scrape_episode(self, url: str) -> list[dict[str, str]]:
        """Scrape an episode page for hoster redirect links."""
        resp = await self._safe_fetch(url, context="episode_page")
        if resp is None:
            return []

        parser = await parse_page(_EpisodePageParser(self.base_url), resp.text)
        return parser.hoster_links

    async def _scrape_all_details(
        self,
        items: list[dict[str, str]],
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Scrape detail pages with bounded concurrency."""
        sem = self._new_semaphore()

        async def _bounded(item: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(item, season=season, episode=episode)

        gathered = await asyncio.gather(
            *[_bounded(item) for item in items],
            return_exceptions=True,
        )

        results: list[SearchResult] = []
        for result in gathered:
            if isinstance(result, SearchResult):
                results.append(result)
        return results

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search aniworld.to and return results with hoster links."""
        if not query:
            return []

        # Category filter: site is anime-only (5070).
        # Accept parent category 5000 (any TV) as well as exact 5070.
        if not self._category_matches(category, 5070):
            return []

        await self._ensure_client()
        await self._verify_domain()

        all_items = relevant_hits(
            await self._ajax_search(query),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
        if not all_items:
            return []

        return await self._scrape_all_details(all_items, season=season, episode=episode)


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags from a string (e.g. <em> from AJAX results)."""
    return LexborHTMLParser(text).text().strip()


plugin = AniworldPlugin()
