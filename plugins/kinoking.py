"""kinoking.cc Python plugin for Scavengarr.

Scrapes kinoking.cc (German streaming site for movies & TV series) with:
- httpx for all requests (server-rendered HTML with embedded JSON)
- Search via /index.php?search={query}&page={n} (50 cards per page); cards
  carry ``data-id``, ``data-type`` (movie/series) and ``data-title``
- Movie pages (/movie.php?id={ID}) embed ``const SERVERS = [...]``: named
  servers with hoster mirrors
- Series pages (/series.php?id={ID}) embed ``const allEpisodesData = [...]``
  with each episode's own hoster links (``video_links``)
- Bounded concurrency for detail page scraping

Movie servers that are skipped: aggregator players (meinecloud, vidsync,
cinesrc), servers tagged with another language than German (``(EN)``,
``(FR)``) and the ``group_kk_*`` servers, which match by title only and mix
up sequels. Each server contributes at most ``_MAX_MIRRORS_PER_SERVER`` links.

No alternative domains found. No authentication required.
"""

from __future__ import annotations

import asyncio
import json
import re
from html.parser import HTMLParser
from typing import Any

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.plugins.categories import served_category
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    relevant_hits,
)

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["kinoking.cc"]
_MAX_PAGES = 20  # 50 cards per page -> ~1000 items
_MAX_MIRRORS_PER_SERVER = 3

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SERVERS_RE = re.compile(r"const SERVERS = (\[.*?\]);\s*\n", re.DOTALL)
_EPISODES_RE = re.compile(r"const allEpisodesData = (\[.*?\]);", re.DOTALL)
_SERIES_TITLE_RE = re.compile(r'seriesTitle = "([^"]*)"')
# "Server A1 (DE)", "VOE (FILMO EN)" -> language code; "VOE (LIVE)" has none
_SERVER_LANG_RE = re.compile(r"\((?:[A-Z]+ )?([A-Z]{2})\)\s*$")
_LINK_SPLIT_RE = re.compile(r"[\s,;|]+")

# Players that aggregate other hosters (or are gone) rather than host a file
_AGGREGATOR_HOSTS = frozenset({"meinecloud", "vidsync", "cinesrc"})
# Servers that match by title only (mix up e.g. Iron Man 1-3)
_TITLE_MATCH_SERVER_PREFIX = "group_kk_"


def _load_json_array(pattern: re.Pattern[str], html: str) -> list[Any]:
    m = pattern.search(html)
    if m is None:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _link(url: str) -> dict[str, str] | None:
    url = url.strip()
    if not url.startswith("http"):
        return None
    hoster = extract_domain(url)
    if not hoster or hoster in _AGGREGATOR_HOSTS:
        return None
    return {"hoster": hoster, "link": url}


def _movie_links(html: str) -> list[dict[str, str]]:
    """Hoster links from a movie page's ``SERVERS`` JSON (German, deduped)."""
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for server in _load_json_array(_SERVERS_RE, html):
        if not isinstance(server, dict):
            continue
        if str(server.get("id", "")).startswith(_TITLE_MATCH_SERVER_PREFIX):
            continue
        lang = _SERVER_LANG_RE.search(str(server.get("name", "")))
        if lang and lang.group(1) != "DE":
            continue
        mirrors = server.get("mirrors")
        if not isinstance(mirrors, list):
            continue
        taken = 0
        for mirror in mirrors:
            link = _link(str(mirror))
            if link is None or link["link"] in seen:
                continue
            seen.add(link["link"])
            links.append(link)
            taken += 1
            if taken == _MAX_MIRRORS_PER_SERVER:
                break
    return links


def _episode_links(ep: dict[str, Any]) -> list[dict[str, str]]:
    """Hoster links of one ``allEpisodesData`` entry."""
    raw = f"{ep.get('video_links') or ''} {ep.get('custom_video_url') or ''}"
    links: list[dict[str, str]] = []
    for url in dict.fromkeys(_LINK_SPLIT_RE.split(raw)):
        link = _link(url)
        if link is not None:
            links.append(link)
    return links


def _pick_episodes(
    episodes: list[Any], season: int | None, episode: int | None
) -> list[dict[str, Any]]:
    """Episodes of *season* (and *episode*); the first season when unset."""
    eps = [e for e in episodes if isinstance(e, dict)]
    seasons = sorted(
        {e["season_number"] for e in eps if isinstance(e.get("season_number"), int)}
    )
    if not seasons:
        return []
    target = season if season is not None else seasons[0]
    picked = [e for e in eps if e.get("season_number") == target]
    if episode is not None:
        picked = [e for e in picked if e.get("episode_number") == episode]
    return sorted(picked, key=lambda e: e.get("episode_number") or 0)


class _SearchCardParser(HTMLParser):
    """Parse kinoking.cc search result cards.

    Cards have structure::

        <div class="group/card relative fav-data-source cursor-pointer"
             data-id="12712" data-type="movie" data-tmdb="1726"
             data-title="Iron Man" data-quality="HD">

    ``data-type`` is ``movie`` or ``series``. The page's JS templates contain
    placeholder cards (``data-id="${...}"``), which are skipped.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._seen: set[tuple[str, str]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        attr_dict = dict(attrs)
        if "fav-data-source" not in (attr_dict.get("class") or "").split():
            return
        card_id = attr_dict.get("data-id") or ""
        card_type = attr_dict.get("data-type") or ""
        title = (attr_dict.get("data-title") or "").strip()
        if not card_id.isdigit() or card_type not in ("movie", "series") or not title:
            return
        if (card_type, card_id) in self._seen:
            return
        self._seen.add((card_type, card_id))
        self.results.append(
            {
                "id": card_id,
                "type": card_type,
                "title": title,
                "tmdb": attr_dict.get("data-tmdb") or "",
                "quality": attr_dict.get("data-quality") or "",
            }
        )


class KinokingPlugin(HttpxPluginBase):
    """Python plugin for kinoking.cc using httpx.

    Scrapes movies and TV series with hoster links.
    """

    name = "kinoking"
    version = "1.1.0"
    provides = "stream"
    _domains = _DOMAINS
    # A cold movie page collects its "(LIVE)" servers first: >15 s at times
    _timeout = 30.0

    async def _search_page(self, query: str, page: int) -> list[dict[str, str]]:
        html = await self._fetch_text(
            f"{self.base_url}/index.php",
            params={"search": query, "page": str(page)},
            context="search",
        )
        if html is None:
            return []
        parser = await self._feed(_SearchCardParser(), html)
        return parser.results

    async def _search_cards(self, query: str) -> list[dict[str, str]]:
        """Collect search cards across pages (up to ``effective_max_results``)."""
        cards: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for page in range(1, _MAX_PAGES + 1):
            page_cards = [
                c
                for c in await self._search_page(query, page)
                if (c["type"], c["id"]) not in seen
            ]
            if not page_cards:
                break
            seen.update((c["type"], c["id"]) for c in page_cards)
            cards.extend(page_cards)
            if len(cards) >= self.effective_max_results:
                break

        self._log.info("kinoking_search", query=query, count=len(cards))
        return cards[: self.effective_max_results]

    async def _build_movie_result(self, card: dict[str, str]) -> SearchResult | None:
        """Load a movie page and build a SearchResult from its servers."""
        source_url = f"{self.base_url}/movie.php?id={card['id']}"
        html = await self._fetch_text(source_url, context="movie")
        if html is None:
            return None

        links = _movie_links(html)
        if not links:
            self._log.debug("kinoking_no_links", url=source_url)
            return None

        return SearchResult(
            title=card["title"],
            download_link=links[0]["link"],
            download_links=links,
            source_url=source_url,
            category=2000,
            metadata={"tmdb": card.get("tmdb", ""), "quality": card.get("quality", "")},
        )

    async def _build_series_results(
        self,
        card: dict[str, str],
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Load a series page and build one SearchResult per episode."""
        source_url = f"{self.base_url}/series.php?id={card['id']}"
        html = await self._fetch_text(source_url, context="series")
        if html is None:
            return []

        m = _SERIES_TITLE_RE.search(html)
        series_title = m.group(1) if m else card["title"]
        episodes = _pick_episodes(_load_json_array(_EPISODES_RE, html), season, episode)

        results: list[SearchResult] = []
        for ep in episodes:
            links = _episode_links(ep)
            if not links:
                continue
            s_num = ep["season_number"]  # int, see _pick_episodes
            e_num = ep.get("episode_number")
            e_tag = f"E{e_num:02d}" if isinstance(e_num, int) else ""
            results.append(
                SearchResult(
                    title=f"{series_title} S{s_num:02d}{e_tag}",
                    download_link=links[0]["link"],
                    download_links=links,
                    source_url=source_url,
                    category=5000,
                    metadata={
                        "series": series_title,
                        "episode_title": str(ep.get("name") or ""),
                        "tmdb": card.get("tmdb", ""),
                    },
                )
            )
        if not results:
            self._log.debug("kinoking_no_links", url=source_url)
        return results

    def _filter_cards(
        self,
        cards: list[dict[str, str]],
        category: int | None,
    ) -> list[dict[str, str]]:
        """Filter search cards by Torznab category (2000, 5000 or None)."""
        if category is None:
            return cards
        kind = "movie" if category == 2000 else "series"
        return [c for c in cards if c["type"] == kind]

    async def _process_cards(
        self,
        cards: list[dict[str, str]],
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Process search cards into SearchResults with bounded concurrency."""
        sem = self._new_semaphore()

        async def _bounded(card: dict[str, str]) -> list[SearchResult]:
            async with sem:
                if card["type"] == "movie":
                    movie = await self._build_movie_result(card)
                    return [movie] if movie else []
                return await self._build_series_results(
                    card, season=season, episode=episode
                )

        gathered = await asyncio.gather(
            *[_bounded(c) for c in cards],
            return_exceptions=True,
        )
        results: list[SearchResult] = []
        for r in gathered:
            if isinstance(r, list):
                results.extend(r)
            elif isinstance(r, Exception):
                self._log.warning("kinoking_detail_error", error=str(r))
        return results[: self.effective_max_results]

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search kinoking.cc and return results with hoster links."""
        if category is None and season is not None:
            category = 5000  # a season request is a series request
        if category is not None:
            # Films 2000, series 5000: anime or HD requests get their parent
            category = served_category(category, (2000, 5000))
            if category is None:
                return []  # the site has films and series only
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        # A cold movie page takes up to 15 s: load only real matches, of
        # the requested kind (a film named like a series takes no slot)
        cards = relevant_hits(
            self._filter_cards(await self._search_cards(query), category),
            query,
            hit_title,
            limit=SINGLE_TITLE_HITS if season is not None else None,
        )
        if not cards:
            return []

        return await self._process_cards(cards, season=season, episode=episode)


plugin = KinokingPlugin()
