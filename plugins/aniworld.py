"""aniworld.to Python plugin for Scavengarr.

Scrapes aniworld.to (German anime streaming site) with:
- httpx for all requests (server-rendered HTML, AJAX search API)
- POST /ajax/search with keyword={query} -> JSON array of matches
- Detail page scraping for metadata (description, genres, cover image)
- Episode page scraping for hoster redirect links (VOE, Filemoon, etc.)
- Category: always 5070 (Anime) since site is anime-only
- Bounded concurrency for detail page scraping

Domain fallback: aniworld.to, aniworld.info
No authentication required.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import urljoin

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "aniworld.to",
    "aniworld.info",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Language key mapping from aniworld.to data-lang-key attributes.
_LANG_MAP: dict[str, str] = {
    "1": "German Dub",
    "2": "English Sub",
    "3": "German Sub",
}


class _DetailPageParser(HTMLParser):
    """Parse aniworld.to anime detail page.

    Extracts:
    - Description from ``.seri_des`` div (via ``data-full-description``)
    - Genres from ``.genres ul li a`` elements
    - Cover image URL from ``.seriesCoverBox img[data-src]``
    - First episode URL from ``table.seasonEpisodesList tbody tr td a``
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        self.description = ""
        self.genres: list[str] = []
        self.cover_url = ""
        self.first_episode_url = ""

        # Description tracking
        self._in_seri_des = False
        self._seri_des_depth = 0
        self._seri_des_text = ""

        # Genre tracking
        self._in_genres_div = False
        self._genres_div_depth = 0
        self._in_genre_ul = False
        self._in_genre_li = False
        self._in_genre_a = False
        self._genre_a_text = ""

        # Episode table tracking
        self._in_episode_table = False
        self._in_tbody = False
        self._in_tr = False
        self._found_first_episode = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # .seri_des div with data-full-description
        if tag == "div" and "seri_des" in classes:
            self._in_seri_des = True
            self._seri_des_depth = 0
            self._seri_des_text = ""
            # Prefer data-full-description if available
            full_desc = attr_dict.get("data-full-description", "")
            if full_desc:
                self.description = full_desc.strip()
        elif tag == "div" and self._in_seri_des:
            self._seri_des_depth += 1

        # .genres div
        if tag == "div" and "genres" in classes:
            self._in_genres_div = True
            self._genres_div_depth = 0
        elif tag == "div" and self._in_genres_div:
            self._genres_div_depth += 1

        if self._in_genres_div and tag == "ul":
            self._in_genre_ul = True
        if self._in_genre_ul and tag == "li":
            self._in_genre_li = True
        if self._in_genre_li and tag == "a":
            self._in_genre_a = True
            self._genre_a_text = ""

        # Cover image: .seriesCoverBox img with data-src
        if tag == "img" and not self.cover_url:
            data_src = attr_dict.get("data-src", "")
            if data_src:
                # Check if parent is seriesCoverBox (we track via class)
                self.cover_url = urljoin(self._base_url, data_src)

        # Episode table
        if tag == "table" and "seasonEpisodesList" in classes:
            self._in_episode_table = True
        if self._in_episode_table and tag == "tbody":
            self._in_tbody = True
        if self._in_tbody and tag == "tr":
            self._in_tr = True

        # First episode link in table
        if self._in_tr and tag == "a" and not self._found_first_episode:
            href = attr_dict.get("href", "") or ""
            if href and "/staffel-" in href and "/episode-" in href:
                self.first_episode_url = urljoin(self._base_url, href)
                self._found_first_episode = True

    def handle_data(self, data: str) -> None:
        if self._in_genre_a:
            self._genre_a_text += data

        if self._in_seri_des and not self.description:
            self._seri_des_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "a" and self._in_genre_a:
            self._in_genre_a = False
            text = self._genre_a_text.strip()
            if text:
                self.genres.append(text)

        if tag == "li" and self._in_genre_li:
            self._in_genre_li = False
        if tag == "ul" and self._in_genre_ul:
            self._in_genre_ul = False

        if tag == "div" and self._in_genres_div:
            if self._genres_div_depth > 0:
                self._genres_div_depth -= 1
            else:
                self._in_genres_div = False

        if tag == "div" and self._in_seri_des:
            if self._seri_des_depth > 0:
                self._seri_des_depth -= 1
            else:
                self._in_seri_des = False
                if not self.description:
                    self.description = self._seri_des_text.strip()

        if tag == "tr" and self._in_tr:
            self._in_tr = False
        if tag == "tbody" and self._in_tbody:
            self._in_tbody = False
        if tag == "table" and self._in_episode_table:
            self._in_episode_table = False


class _EpisodePageParser(HTMLParser):
    """Parse aniworld.to episode page for hoster links.

    Extracts hoster redirect links from::

        <li data-lang-key="1" data-link-id="123"
            data-link-target="/redirect/123">
          <div class="watchEpisode">
            <a ...><h4>VOE</h4></a>
          </div>
        </li>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url
        self.hoster_links: list[dict[str, str]] = []

        self._current_li_lang_key = ""
        self._current_li_redirect = ""
        self._in_hoster_li = False
        self._in_h4 = False
        self._h4_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        # <li data-lang-key="..." data-link-target="/redirect/...">
        if tag == "li":
            lang_key = attr_dict.get("data-lang-key", "")
            link_target = attr_dict.get("data-link-target", "")
            if lang_key and link_target:
                self._in_hoster_li = True
                self._current_li_lang_key = lang_key
                self._current_li_redirect = link_target

        # <h4> inside hoster li (contains hoster name)
        if self._in_hoster_li and tag == "h4":
            self._in_h4 = True
            self._h4_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_h4:
            self._h4_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h4" and self._in_h4:
            self._in_h4 = False
            hoster_name = self._h4_text.strip()
            if hoster_name and self._current_li_redirect:
                lang_label = _LANG_MAP.get(
                    self._current_li_lang_key,
                    self._current_li_lang_key,
                )
                self.hoster_links.append(
                    {
                        "hoster": hoster_name.lower(),
                        "link": urljoin(self._base_url, self._current_li_redirect),
                        "language": lang_label,
                    }
                )

        if tag == "li" and self._in_hoster_li:
            self._in_hoster_li = False
            self._current_li_lang_key = ""
            self._current_li_redirect = ""


class AniworldPlugin(HttpxPluginBase):
    """Python plugin for aniworld.to using httpx."""

    name = "aniworld"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"
    default_language = "de"

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
        if resp is None:
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
            if title and link:
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
        return results[: self._max_results]

    async def _scrape_detail(
        self,
        item: dict[str, str],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Scrape anime detail page and episode for hoster links.

        When *season* and *episode* are given the plugin navigates directly
        to ``/anime/{slug}/staffel-{season}/episode-{episode}`` instead of
        scraping the first episode on the detail page.
        """
        detail_url = item["link"]
        if not detail_url.startswith("http"):
            detail_url = urljoin(self.base_url, detail_url)

        # Fetch detail page
        resp = await self._safe_fetch(detail_url, context="detail_page")
        if resp is None:
            return None

        detail_parser = _DetailPageParser(self.base_url)
        detail_parser.feed(resp.text)

        # Determine which episode page to scrape
        hoster_links: list[dict[str, str]] = []
        if season is not None and episode is not None:
            # Build a direct episode URL from the detail page slug
            slug = detail_url.rstrip("/").rsplit("/", 1)[-1]
            ep_url = f"{self.base_url}/anime/{slug}/staffel-{season}/episode-{episode}"
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

        parser = _EpisodePageParser(self.base_url)
        parser.feed(resp.text)
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
        # If a non-anime category is requested, return empty.
        if category is not None and category != 5070:
            return []

        await self._ensure_client()
        await self._verify_domain()

        all_items = await self._ajax_search(query)
        if not all_items:
            return []

        return await self._scrape_all_details(all_items, season=season, episode=episode)


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags from a string (e.g. <em> from AJAX results)."""

    class _TagStripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

    stripper = _TagStripper()
    stripper.feed(text)
    return "".join(stripper.parts).strip()


plugin = AniworldPlugin()
