"""serienfans.org Python plugin for Scavengarr.

Scrapes serienfans.org (German TV series DDL site) with:
- httpx for all requests (no Cloudflare challenge)
- JSON search API: GET /api/v2/search?q={query}&ql=DE
- Server-rendered series pages at /{url_id} with metadata + series_id
- Season API: GET /api/v1/{series_id}/season/{n}?lang=ALL returns JSON with
  HTML release entries containing scene names and download links
- Download links via /external/2/{hash} redirect URLs
- TV series only (category 5000)
- Season/episode filtering support

No authentication required. No active alternative domains.
"""

from __future__ import annotations

import asyncio
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.dom import ancestors, classes
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["serienfans.org"]

# Index letters for browse mode (empty query)
_INDEX_LETTERS = list("abcdefghijklmnopqrstuvwxyz") + ["0-9"]

# Max series to process from index pages during browse
_MAX_BROWSE_SERIES = 50

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TV_CATEGORIES = frozenset({5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080})
_MOVIE_CATEGORIES = frozenset({2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060})

# Regex to extract initSeason('series_id', ...) from detail page HTML
_INIT_SEASON_RE = re.compile(r"initSeason\(\s*'([^']+)'")

# Regex to extract year from <h2>Title <i>(2008)</i></h2>
_TITLE_YEAR_RE = re.compile(r"<h2>\s*(.+?)\s*<i>\((\d{4})\)</i>\s*</h2>")

# Regex to extract IMDB URL
_IMDB_RE = re.compile(r'href="(https://www\.imdb\.com/title/[^"]+)"')

# Regex to extract season count: <strong>Staffeln</strong>\n<span>6</span>
_SEASONS_RE = re.compile(r"<strong>Staffeln</strong>\s*<span>(\d+)</span>", re.DOTALL)

# Regex to extract runtime: <strong>Laufzeit</strong>\n<span>45min</span>
_RUNTIME_RE = re.compile(r"<strong>Laufzeit</strong>\s*<span>([^<]+)</span>", re.DOTALL)

# Regex to extract rating: <i class="rating ...">9.5</i>
_RATING_RE = re.compile(r'<i class="rating[^"]*">([^<]+)</i>')

# Regex to extract genre links: <a class="genre" href="/genre/18">Drama</a>
_GENRE_RE = re.compile(r'<a class="genre" href="/genre/\d+">([^<]+)</a>')

# Regex to extract description from og:description meta tag
_DESC_RE = re.compile(r'<meta property="og:description" content="([^"]*)"', re.DOTALL)

# Regex to extract cover image: <img src="/media/1590003296583/200/300">
_COVER_RE = re.compile(r'<i class="cover"><img src="([^"]+)"')


class _ReleaseParser:
    """Parse release entries from serienfans.org season API HTML (selectolax).

    The season API returns JSON with an ``html`` field containing release
    entries. Each release is a ``<div class="entry">`` containing:
    - ``<h3>`` with season info, quality, size
    - ``<small>`` with scene release name
    - ``<a class="dlb row" href="/external/2/{hash}">`` download links
    - ``<div class="list simple">`` with per-episode download links

    Each row of the episode list below its ``head`` row is an episode:
    number and title in its first two ``<div>`` cells, then its download
    links. The entry's other links belong to the season pack; release name
    and size count outside the episode list only.
    """

    def __init__(self, base_url: str) -> None:
        self.releases: list[dict[str, str | list[dict[str, str]]]] = []
        self.episodes: list[dict[str, str | list[dict[str, str]]]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for entry in tree.css("div.entry"):
            # An entry inside another one is part of the outer entry
            if not any(
                parent.tag == "div" and "entry" in classes(parent)
                for parent in ancestors(entry)
            ):
                self._add_entry(entry)

    def _add_entry(self, entry: LexborNode) -> None:
        """Add the episodes of *entry*, and its release when it has links."""
        rows = _episode_rows(entry)
        for row in rows:
            self._add_episode(row)
        # The last release name and size outside the episode list count
        lists = {node.mem_id for node in entry.css("div.list.simple")}
        release_name = size = ""
        for node in entry.css("small, span.morespec"):
            if _inside(node, lists):
                continue
            if node.tag == "small":
                release_name = node.text().strip()
            else:
                size = node.text().strip()
        # The links outside the episode rows are the season pack's
        row_ids = {row.mem_id for row in rows}
        download_links = [
            link
            for anchor in entry.css("a.dlb")
            if not _inside(anchor, row_ids) and (link := self._link(anchor))
        ]
        if download_links:
            self.releases.append(
                {
                    "release_name": release_name,
                    "size": size,
                    "download_links": download_links,
                }
            )

    def _add_episode(self, row: LexborNode) -> None:
        """Add the episode of *row* when it has a number and links."""
        # Cells: number ("1."), title, links; their stripped texts are joined
        cells = [cell for cell in row.iter() if cell.tag == "div"]
        number = cells[0].text(strip=True).rstrip(".") if cells else ""
        title = cells[1].text(strip=True) if len(cells) > 1 else ""
        links = [link for anchor in row.css("a.dlb") if (link := self._link(anchor))]
        if number and links:
            self.episodes.append(
                {
                    "episode_num": number,
                    "episode_title": title,
                    "download_links": links,
                }
            )

    def _link(self, anchor: LexborNode) -> dict[str, str] | None:
        """The link of an ``a.dlb``, named by its (last) ``<span>``."""
        href = anchor.attributes.get("href") or ""
        spans = anchor.css("span")
        hoster = spans[-1].text().strip() if spans else ""
        if not href or not hoster:
            return None
        if href.startswith("/"):
            href = f"{self._base_url}{href}"
        return {"hoster": hoster, "link": href}


def _episode_rows(entry: LexborNode) -> list[LexborNode]:
    """The episode rows of *entry*: its episode list's rows below the head.

    The rows inside an episode row (its link box) belong to it.
    """
    rows: list[LexborNode] = []
    for row in entry.css("div.list.simple div.row"):
        if "head" in classes(row) or (rows and _inside(row, {rows[-1].mem_id})):
            continue
        rows.append(row)
    return rows


def _inside(node: LexborNode, containers: set[int]) -> bool:
    """Whether an ancestor of *node* is one of *containers* (``mem_id``)."""
    return any(parent.mem_id in containers for parent in ancestors(node))


class _IndexPageParser:
    """Parse the index page to extract series url_ids and titles (selectolax).

    Structure: ``<a href="/{url_id}"><strong>Title</strong><small>(year)</small></a>``
    """

    def __init__(self) -> None:
        self.series: list[dict[str, str]] = []

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        for link in tree.css("a[href^='/']"):
            href = link.attributes.get("href") or ""
            # Match series links like /breaking-bad (single path segment, no dots)
            if href.count("/") != 1 or "." in href:
                continue
            url_id = href.lstrip("/")
            # The link's <strong> texts make the title, its <small> texts the year
            title = "".join(strong.text() for strong in link.css("strong")).strip()
            year = "".join(small.text() for small in link.css("small"))
            if url_id and title:
                self.series.append(
                    {"url_id": url_id, "title": title, "year": year.strip().strip("()")}
                )


class SerienfansPlugin(HttpxPluginBase):
    """Python plugin for serienfans.org using httpx."""

    name = "serienfans"
    version = "1.0.0"
    mode = "httpx"
    provides = "download"

    _domains = _DOMAINS

    # ------------------------------------------------------------------
    # Search API
    # ------------------------------------------------------------------

    async def _search_api(self, query: str) -> list[dict[str, str | int]]:
        """Execute JSON search API and return series entries."""
        body = await self._fetch_text(
            f"{self.base_url}/api/v2/search",
            params={"q": query, "ql": "DE"},
            context="search",
        )
        data = self._parse_json_text(body, "search")
        if data is None:
            return []

        series = data.get("result", [])
        if not isinstance(series, list):
            return []

        self._log.info("serienfans_search_api", query=query, count=len(series))
        return series

    # ------------------------------------------------------------------
    # Detail page parsing
    # ------------------------------------------------------------------

    async def _fetch_detail_page(
        self, url_id: str
    ) -> dict[str, str | int | list[str]] | None:
        """Fetch a series detail page and extract metadata + series_id.

        Returns dict with keys: series_id, title, year, genres, imdb_url,
        rating, runtime, seasons_count, description, cover_url.
        Returns None on failure.
        """
        html = await self._fetch_text(f"{self.base_url}/{url_id}", context=url_id)
        if html is None:
            return None

        # Extract series_id from initSeason() call
        m = _INIT_SEASON_RE.search(html)
        if not m:
            self._log.warning("serienfans_no_series_id", url_id=url_id)
            return None

        series_id = m.group(1)

        # Extract metadata
        title_match = _TITLE_YEAR_RE.search(html)
        title = title_match.group(1).strip() if title_match else url_id
        year = title_match.group(2) if title_match else ""

        imdb_match = _IMDB_RE.search(html)
        imdb_url = imdb_match.group(1) if imdb_match else ""

        seasons_match = _SEASONS_RE.search(html)
        seasons_count = int(seasons_match.group(1)) if seasons_match else 0

        runtime_match = _RUNTIME_RE.search(html)
        runtime = runtime_match.group(1).strip() if runtime_match else ""

        rating_match = _RATING_RE.search(html)
        rating = rating_match.group(1).strip() if rating_match else ""

        genres = _GENRE_RE.findall(html)

        desc_match = _DESC_RE.search(html)
        description = desc_match.group(1) if desc_match else ""

        cover_match = _COVER_RE.search(html)
        cover_url = cover_match.group(1) if cover_match else ""

        return {
            "series_id": series_id,
            "title": title,
            "year": year,
            "genres": genres,
            "imdb_url": imdb_url,
            "rating": rating,
            "runtime": runtime,
            "seasons_count": seasons_count,
            "description": description,
            "cover_url": cover_url,
        }

    # ------------------------------------------------------------------
    # Season API
    # ------------------------------------------------------------------

    async def _fetch_season(
        self,
        series_id: str,
        season: int | str,
    ) -> tuple[list[dict], list[dict]]:
        """Fetch season releases via the API.

        Returns (releases, episodes) tuple.
        """
        context = f"{series_id}:season:{season}"
        body = await self._fetch_text(
            f"{self.base_url}/api/v1/{series_id}/season/{season}",
            params={"lang": "ALL"},
            context=context,
        )
        data = self._parse_json_text(body, context)
        if data is None:
            return [], []

        html = data.get("html", "")
        if not html:
            return [], []

        parser = _ReleaseParser(self.base_url)
        parser.feed(html)

        self._log.info(
            "serienfans_season_parsed",
            series_id=series_id,
            season=season,
            releases=len(parser.releases),
            episodes=len(parser.episodes),
        )
        return parser.releases, parser.episodes

    # ------------------------------------------------------------------
    # Build results
    # ------------------------------------------------------------------

    def _build_search_result(
        self,
        series_meta: dict,
        release: dict,
        url_id: str,
    ) -> SearchResult:
        """Convert a series + release entry to a SearchResult."""
        title = str(series_meta.get("title", ""))
        year = str(series_meta.get("year", ""))
        release_name = str(release.get("release_name", ""))
        size = str(release.get("size", "")) or None
        dl_links = release.get("download_links", [])
        dl_links_list = dl_links if isinstance(dl_links, list) else []

        # Use release name as title (scene name is more informative)
        display_title = release_name or title

        # First download link as primary
        primary_link = dl_links_list[0]["link"] if dl_links_list else ""

        source_url = f"{self.base_url}/{url_id}"

        return SearchResult(
            title=display_title,
            download_link=primary_link,
            download_links=dl_links_list if dl_links_list else None,
            source_url=source_url,
            release_name=release_name or None,
            size=size,
            published_date=year if year else None,
            category=5000,
        )

    def _build_episode_result(
        self,
        series_meta: dict,
        episode: dict,
        url_id: str,
        season: int | None = None,
    ) -> SearchResult:
        """Convert a series + episode entry to a SearchResult.

        The title uses ``SxxEyy`` so Sonarr can parse it (``E5`` alone it
        cannot); without a known season only ``Eyy`` is given.
        """
        title = str(series_meta.get("title", ""))
        year = str(series_meta.get("year", ""))
        ep_num = str(episode.get("episode_num", ""))
        ep_title = str(episode.get("episode_title", ""))
        dl_links = episode.get("download_links", [])
        dl_links_list = dl_links if isinstance(dl_links, list) else []

        ep_code = f"E{int(ep_num):02d}" if ep_num.isdigit() else f"E{ep_num}"
        if season is not None:
            ep_code = f"S{season:02d}{ep_code}"
        display_title = f"{title} {ep_code}"
        if ep_title:
            display_title = f"{display_title} - {ep_title}"

        primary_link = dl_links_list[0]["link"] if dl_links_list else ""
        source_url = f"{self.base_url}/{url_id}"

        return SearchResult(
            title=display_title,
            download_link=primary_link,
            download_links=dl_links_list if dl_links_list else None,
            source_url=source_url,
            published_date=year if year else None,
            category=5000,
        )

    # ------------------------------------------------------------------
    # Browse (empty query)
    # ------------------------------------------------------------------

    async def _browse_index(self, letter: str) -> list[dict[str, str]]:
        """Fetch an index page and return series entries."""
        html = await self._fetch_text(
            f"{self.base_url}/index/{letter}", context=f"index:{letter}"
        )
        if html is None:
            return []

        parser = _IndexPageParser()
        parser.feed(html)
        return parser.series

    # ------------------------------------------------------------------
    # Series processing
    # ------------------------------------------------------------------

    async def _process_series(
        self,
        sem: asyncio.Semaphore,
        url_id: str,
        season: int | None,
        episode: int | None,
    ) -> list[SearchResult]:
        """Fetch detail + season data for one series, return results."""
        async with sem:
            meta = await self._fetch_detail_page(url_id)
            if not meta:
                return []

            series_id = str(meta.get("series_id", ""))
            if not series_id:
                return []

            season_param: int | str = season if season is not None else "ALL"
            releases, episodes = await self._fetch_season(series_id, season_param)

        # Episode filter
        if episode is not None:
            return self._filter_episodes(meta, episodes, episode, url_id, season)

        # Season pack releases
        return [
            sr
            for release in releases
            if (sr := self._build_search_result(meta, release, url_id))
            and sr.download_link
        ]

    def _filter_episodes(
        self,
        meta: dict,
        episodes: list[dict],
        episode: int,
        url_id: str,
        season: int | None = None,
    ) -> list[SearchResult]:
        """Return only episodes matching the given episode number."""
        ep_str = str(episode)
        out: list[SearchResult] = []
        for ep in episodes:
            if str(ep.get("episode_num", "")).strip() == ep_str:
                sr = self._build_episode_result(meta, ep, url_id, season)
                if sr.download_link:
                    out.append(sr)
        return out

    # ------------------------------------------------------------------
    # Item collection
    # ------------------------------------------------------------------

    async def _collect_items(self, query: str) -> list[dict]:
        """Return url_id dicts from search API or index browse."""
        if query:
            series_list = await self._search_api(query)
            return [
                {"url_id": str(s.get("url_id", "")), "year": s.get("year")}
                for s in series_list
                if s.get("url_id")
            ]

        # Browse mode: fetch index pages
        items: list[dict] = []
        for letter in _INDEX_LETTERS:
            entries = await self._browse_index(letter)
            items.extend(
                {"url_id": e["url_id"], "year": e.get("year")} for e in entries
            )
            if len(items) >= _MAX_BROWSE_SERIES:
                break
        return items[:_MAX_BROWSE_SERIES]

    # ------------------------------------------------------------------
    # Main search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search serienfans.org and return results."""
        if category is not None and category in _MOVIE_CATEGORIES:
            return []

        await self._ensure_client()

        items = await self._collect_items(query)
        if not items:
            return []

        sem = self._new_semaphore()
        tasks = [
            self._process_series(sem, str(item["url_id"]), season, episode)
            for item in items
        ]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for series_results in task_results:
            results.extend(series_results)
            if len(results) >= self.effective_max_results:
                break

        # /external/2/<hash> links sit behind Cloudflare: resolve them to the
        # hoster / link container (only for the results returned)
        return await self._resolve_result_links(results[: self.effective_max_results])


plugin = SerienfansPlugin()
