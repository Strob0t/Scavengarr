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
import json
import re
from html.parser import HTMLParser

from scavengarr.domain.plugins.base import SearchResult
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


class _ReleaseParser(HTMLParser):
    """Parse release entries from serienfans.org season API HTML.

    The season API returns JSON with an ``html`` field containing release
    entries. Each release is a ``<div class="entry">`` containing:
    - ``<h3>`` with season info, quality, size
    - ``<small>`` with scene release name
    - ``<a class="dlb row" href="/external/2/{hash}">`` download links
    - ``<div class="list simple">`` with per-episode download links

    Uses a single div depth counter for the entry, with boolean flags
    to track which context (episode list, episode row) we're in.
    The ``<h3>`` tag may contain invalid nested ``<div>`` elements
    which are tracked separately to avoid depth miscount.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.releases: list[dict[str, str | list[dict[str, str]]]] = []
        self._base_url = base_url

        # Entry tracking — single depth counter for all divs
        self._in_entry = False
        self._entry_div_depth = 0

        # Track <h3> to ignore <div> tags inside it (invalid HTML nesting)
        self._in_h3 = False
        self._h3_div_depth = 0

        # Scene release name (in <small> inside entry)
        self._in_small = False
        self._current_release_name = ""

        # Quality/size from <span class="morespec">
        self._in_morespec = False
        self._current_morespec = ""

        # Download links (complete season packs)
        self._in_dlb_link = False
        self._current_dl_href = ""
        self._in_dlb_span = False
        self._dlb_span_text = ""
        self._current_download_links: list[dict[str, str]] = []

        # Episode list context (flag only, depth via _entry_div_depth)
        self._in_episode_list = False
        self._episode_list_depth = 0  # entry_div_depth when list started

        # Episode row context
        self._in_episode_row = False
        self._episode_row_depth = 0  # entry_div_depth when row started
        self._episode_cell_index = 0
        self._current_episode_num = ""
        self._current_episode_title = ""
        self._current_episode_links: list[dict[str, str]] = []
        self.episodes: list[dict[str, str | list[dict[str, str]]]] = []

    def _reset_entry(self) -> None:
        self._current_release_name = ""
        self._current_morespec = ""
        self._current_download_links = []
        self._in_episode_list = False

    def _emit_entry(self) -> None:
        if not self._current_download_links:
            return
        self.releases.append(
            {
                "release_name": self._current_release_name,
                "size": self._current_morespec,
                "download_links": self._current_download_links.copy(),
            }
        )

    def _emit_episode(self) -> None:
        num = self._current_episode_num.strip().rstrip(".")
        title = self._current_episode_title.strip()
        if num and self._current_episode_links:
            self.episodes.append(
                {
                    "episode_num": num,
                    "episode_title": title,
                    "download_links": self._current_episode_links.copy(),
                }
            )

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Track <h3> to ignore nested <div> (invalid HTML on serienfans)
        if tag == "h3":
            self._in_h3 = True
            self._h3_div_depth = 0

        # <div> inside <h3> — track separately, don't count for entry depth
        if tag == "div" and self._in_h3:
            self._h3_div_depth += 1
            return

        # All divs: single depth counter
        if tag == "div":
            if self._in_entry:
                self._entry_div_depth += 1
                # Detect episode list start
                if "list" in classes and "simple" in classes:
                    self._in_episode_list = True
                    self._episode_list_depth = self._entry_div_depth
                # Detect episode row start (inside list, not head)
                elif (
                    self._in_episode_list
                    and "row" in classes
                    and "head" not in classes
                    and not self._in_episode_row
                ):
                    self._in_episode_row = True
                    self._episode_row_depth = self._entry_div_depth
                    self._episode_cell_index = 0
                    self._current_episode_num = ""
                    self._current_episode_title = ""
                    self._current_episode_links = []
            elif "entry" in classes:
                self._in_entry = True
                self._entry_div_depth = 0
                self._reset_entry()

        if not self._in_entry:
            return

        # Scene release name: <small> inside entry (not inside episode list)
        if tag == "small" and not self._in_episode_list:
            self._in_small = True
            self._current_release_name = ""

        # Quality/size: <span class="morespec">
        if tag == "span" and "morespec" in classes and not self._in_episode_list:
            self._in_morespec = True
            self._current_morespec = ""

        # Download link: <a class="dlb row" href="/external/...">
        if tag == "a" and "dlb" in classes:
            href = attr_dict.get("href", "") or ""
            if href:
                self._in_dlb_link = True
                if href.startswith("/"):
                    self._current_dl_href = f"{self._base_url}{href}"
                else:
                    self._current_dl_href = href
                self._dlb_span_text = ""

        # Hoster name: <span> inside dlb link
        if tag == "span" and self._in_dlb_link:
            self._in_dlb_span = True
            self._dlb_span_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_small and not self._in_episode_list:
            self._current_release_name += data

        if self._in_morespec:
            self._current_morespec += data

        if self._in_dlb_span:
            self._dlb_span_text += data

        # Episode row cell data
        if self._in_episode_row and not self._in_dlb_link:
            stripped = data.strip()
            if stripped:
                if self._episode_cell_index == 0:
                    self._current_episode_num += stripped
                elif self._episode_cell_index == 1:
                    self._current_episode_title += stripped

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        # Handle </h3> and </div> inside <h3>
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
            return

        if tag == "div" and self._in_h3:
            if self._h3_div_depth > 0:
                self._h3_div_depth -= 1
            return

        if tag == "small" and self._in_small:
            self._in_small = False
            self._current_release_name = self._current_release_name.strip()

        if tag == "span":
            if self._in_morespec:
                self._in_morespec = False
                self._current_morespec = self._current_morespec.strip()

            if self._in_dlb_span:
                self._in_dlb_span = False

        if tag == "a" and self._in_dlb_link:
            self._in_dlb_link = False
            hoster = self._dlb_span_text.strip()
            if hoster and self._current_dl_href:
                link_entry = {"hoster": hoster, "link": self._current_dl_href}
                if self._in_episode_row:
                    self._current_episode_links.append(link_entry)
                else:
                    self._current_download_links.append(link_entry)
            self._current_dl_href = ""

        if tag == "div" and self._in_entry:
            # Check if we're closing an episode row
            if (
                self._in_episode_row
                and self._entry_div_depth == self._episode_row_depth
            ):
                self._emit_episode()
                self._in_episode_row = False

            # Check if we're closing the episode list
            if (
                self._in_episode_list
                and self._entry_div_depth == self._episode_list_depth
            ):
                self._in_episode_list = False

            # Track cell transitions in episode rows: closing a direct
            # child div of the row means the cell is done.
            if (
                self._in_episode_row
                and self._entry_div_depth == self._episode_row_depth + 1
            ):
                self._episode_cell_index += 1

            # Decrement depth
            if self._entry_div_depth > 0:
                self._entry_div_depth -= 1
            else:
                # Entry closed
                self._in_entry = False
                self._in_episode_list = False
                self._in_episode_row = False
                self._emit_entry()


class _IndexPageParser(HTMLParser):
    """Parse the index page to extract series url_ids and titles.

    Structure: ``<a href="/{url_id}"><strong>Title</strong><small>(year)</small></a>``
    """

    def __init__(self) -> None:
        super().__init__()
        self.series: list[dict[str, str]] = []
        self._in_link = False
        self._current_url_id = ""
        self._in_strong = False
        self._current_title = ""
        self._in_small = False
        self._current_year = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "a":
            href = attr_dict.get("href", "") or ""
            # Match series links like /breaking-bad (single path segment, no dots)
            if href.startswith("/") and href.count("/") == 1 and "." not in href:
                self._in_link = True
                self._current_url_id = href.lstrip("/")
                self._current_title = ""
                self._current_year = ""

        if tag == "strong" and self._in_link:
            self._in_strong = True

        if tag == "small" and self._in_link:
            self._in_small = True

    def handle_data(self, data: str) -> None:
        if self._in_strong:
            self._current_title += data
        if self._in_small:
            self._current_year += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong" and self._in_strong:
            self._in_strong = False

        if tag == "small" and self._in_small:
            self._in_small = False

        if tag == "a" and self._in_link:
            self._in_link = False
            if self._current_url_id and self._current_title.strip():
                year = self._current_year.strip().strip("()")
                self.series.append(
                    {
                        "url_id": self._current_url_id,
                        "title": self._current_title.strip(),
                        "year": year,
                    }
                )


class SerienfansPlugin(HttpxPluginBase):
    """Python plugin for serienfans.org using httpx."""

    name = "serienfans"
    version = "1.0.0"
    mode = "httpx"
    provides = "download"
    default_language = "de"

    _domains = _DOMAINS

    # ------------------------------------------------------------------
    # Search API
    # ------------------------------------------------------------------

    async def _search_api(self, query: str) -> list[dict[str, str | int]]:
        """Execute JSON search API and return series entries."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/api/v2/search",
                params={"q": query, "ql": "DE"},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("serienfans_search_failed", query=query, error=str(exc))
            return []

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            self._log.warning("serienfans_invalid_json", query=query)
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
        client = await self._ensure_client()
        url = f"{self.base_url}/{url_id}"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("serienfans_detail_failed", url_id=url_id, error=str(exc))
            return None

        html = resp.text

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
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/api/v1/{series_id}/season/{season}",
                params={"lang": "ALL"},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "serienfans_season_failed",
                series_id=series_id,
                season=season,
                error=str(exc),
            )
            return [], []

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            self._log.warning(
                "serienfans_season_invalid_json",
                series_id=series_id,
                season=season,
            )
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
    ) -> SearchResult:
        """Convert a series + episode entry to a SearchResult."""
        title = str(series_meta.get("title", ""))
        year = str(series_meta.get("year", ""))
        ep_num = str(episode.get("episode_num", ""))
        ep_title = str(episode.get("episode_title", ""))
        dl_links = episode.get("download_links", [])
        dl_links_list = dl_links if isinstance(dl_links, list) else []

        display_title = f"{title} - E{ep_num}"
        if ep_title:
            display_title = f"{title} - E{ep_num} - {ep_title}"

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
        client = await self._ensure_client()
        url = f"{self.base_url}/index/{letter}"

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("serienfans_index_failed", letter=letter, error=str(exc))
            return []

        parser = _IndexPageParser()
        parser.feed(resp.text)
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
            return self._filter_episodes(meta, episodes, episode, url_id)

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
    ) -> list[SearchResult]:
        """Return only episodes matching the given episode number."""
        ep_str = str(episode)
        out: list[SearchResult] = []
        for ep in episodes:
            if str(ep.get("episode_num", "")).strip() == ep_str:
                sr = self._build_episode_result(meta, ep, url_id)
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

        return results[: self.effective_max_results]


plugin = SerienfansPlugin()
