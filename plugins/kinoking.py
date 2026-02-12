"""kinoking.cc Python plugin for Scavengarr.

Scrapes kinoking.cc (German streaming site for movies & TV series) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Search via /index.php?search={query} (all results on one page)
- Movie detail pages at /movie.php?id={ID} with iframe hoster URLs
- Series detail pages at /series.php?id={ID} with season/episode info
- Episode hoster links via JSON API /api/episode-navigation.php?episode_id={ID}
- Bounded concurrency for detail page scraping

No alternative domains found. No authentication required.
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
_DOMAINS = ["kinoking.cc"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Genre name (lowercase, German) → Torznab category.
_GENRE_CATEGORY_MAP: dict[str, int] = {
    # Movies
    "action": 2000,
    "abenteuer": 2000,
    "krimi": 2000,
    "drama": 2000,
    "familie": 2000,
    "fantasy": 2000,
    "historie": 2000,
    "horror": 2000,
    "komödie": 2000,
    "kriegsfilm": 2000,
    "musik": 2000,
    "mystery": 2000,
    "romanze": 2000,
    "science fiction": 2000,
    "thriller": 2000,
    "western": 2000,
    # TV-specific
    "animation": 5070,
    "anime": 5070,
    "dokumentarfilm": 5080,
    "dokumentation": 5080,
}


def _genre_to_torznab(genre: str, is_series: bool) -> int:
    """Map genre name to Torznab category."""
    key = genre.lower().strip()
    mapped = _GENRE_CATEGORY_MAP.get(key)
    if mapped is not None:
        # Animation/anime/documentary keep their specific IDs
        if mapped >= 5000:
            return mapped
        # For series, shift to TV category
        return 5000 if is_series else mapped
    return 5000 if is_series else 2000


def _determine_category(
    genres: list[str],
    is_series: bool,
    category: int | None,
) -> int:
    """Determine Torznab category from genres, with caller override."""
    if category is not None:
        return category
    for genre in genres:
        mapped = _genre_to_torznab(genre, is_series)
        if is_series and mapped != 5000:
            return mapped
        if not is_series and mapped != 2000:
            return mapped
    return 5000 if is_series else 2000


class _SearchCardParser(HTMLParser):
    """Parse kinoking.cc search results page for content cards.

    Cards have structure::

        <div class="content-card" onclick="playMovie(25656)">
          <img class="card-poster" alt="Title">
          <div class="content-type-badge badge-movie">Film</div>
          <div class="card-title">Title</div>
          <div class="card-rating"><i class="fas fa-star"></i>7.9</div>
        </div>

    For series::

        <div class="content-card" onclick="playContent(2098, 'tv', 7515)">
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []

        self._in_card = False
        self._card_onclick = ""
        self._in_badge = False
        self._badge_text = ""
        self._in_title_div = False
        self._title_text = ""
        self._in_rating_div = False
        self._rating_text = ""
        self._card_div_depth = 0

    def _reset_card(self) -> None:
        self._card_onclick = ""
        self._badge_text = ""
        self._title_text = ""
        self._rating_text = ""

    def _emit_card(self) -> None:
        onclick = self._card_onclick
        title = self._title_text.strip()
        badge = self._badge_text.strip().lower()
        if not onclick or not title:
            return

        entry: dict[str, str] = {"title": title, "badge": badge}
        entry["rating"] = self._rating_text.strip()

        # Parse onclick to get type + ID
        m_movie = re.match(r"playMovie\((\d+)\)", onclick)
        m_content = re.match(r"playContent\(\d+,\s*'(\w+)',\s*(\d+)\)", onclick)
        if m_movie:
            entry["type"] = "movie"
            entry["id"] = m_movie.group(1)
        elif m_content:
            content_type = m_content.group(1)
            entry["type"] = "series" if content_type == "tv" else "movie"
            entry["id"] = m_content.group(2)
        else:
            return

        self.results.append(entry)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        if tag == "div":
            if self._in_card:
                self._card_div_depth += 1
            elif "content-card" in classes:
                onclick = attr_dict.get("onclick", "") or ""
                if onclick:
                    self._in_card = True
                    self._card_div_depth = 0
                    self._reset_card()
                    self._card_onclick = onclick

            if self._in_card and "content-type-badge" in classes:
                self._in_badge = True
                self._badge_text = ""
            if self._in_card and "card-title" in classes:
                self._in_title_div = True
                self._title_text = ""
            if self._in_card and "card-rating" in classes:
                self._in_rating_div = True
                self._rating_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_badge:
            self._badge_text += data
        if self._in_title_div:
            self._title_text += data
        if self._in_rating_div:
            self._rating_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._in_badge:
            self._in_badge = False
        if self._in_title_div:
            self._in_title_div = False
        if self._in_rating_div:
            self._in_rating_div = False
        if self._in_card:
            if self._card_div_depth > 0:
                self._card_div_depth -= 1
            else:
                self._in_card = False
                self._emit_card()


class _MovieDetailParser(HTMLParser):
    """Parse kinoking.cc movie detail page for hoster links and metadata.

    Movie page structure::

        <div class="movie-player">
          <iframe src="https://voe.sx/e/xxx"></iframe>
        </div>
        <div class="movie-link-grid">
          <a href="?id=9456&link=9414" class="movie-link-btn">
            <span>Voe</span>
          </a>
          <a href="?id=9456&link=9415" class="movie-link-btn">
            <span>Vidhideplus</span>
          </a>
        </div>
        <h1>Batman Begins</h1>
        <div class="genre-badges">
          <span class="genre-badge genre-action">Action</span>
        </div>
    """

    def __init__(self) -> None:
        super().__init__()
        self.iframe_src = ""
        self.title = ""
        self.genres: list[str] = []
        self.server_links: list[dict[str, str]] = []

        self._in_h1 = False
        self._h1_text = ""
        self._in_link_btn = False
        self._link_btn_href = ""
        self._in_link_span = False
        self._link_span_text = ""
        self._in_genre_span = False
        self._genre_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        if tag == "iframe" and not self.iframe_src:
            src = attr_dict.get("src", "") or ""
            if src and src.startswith("http"):
                self.iframe_src = src

        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        if tag == "a" and "movie-link-btn" in classes:
            href = attr_dict.get("href", "") or ""
            self._in_link_btn = True
            self._link_btn_href = href
            self._link_span_text = ""

        if tag == "span" and self._in_link_btn:
            if "premium-badge" not in classes:
                self._in_link_span = True
                self._link_span_text = ""

        if tag == "span" and any(c.startswith("genre-") for c in classes):
            self._in_genre_span = True
            self._genre_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_link_span:
            self._link_span_text += data
        if self._in_genre_span:
            self._genre_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = self._h1_text.strip()

        if tag == "span":
            if self._in_link_span:
                self._in_link_span = False
            if self._in_genre_span:
                self._in_genre_span = False
                genre = self._genre_text.strip()
                if genre and genre not in self.genres:
                    self.genres.append(genre)

        if tag == "a" and self._in_link_btn:
            self._in_link_btn = False
            name = self._link_span_text.strip()
            href = self._link_btn_href
            if name and href:
                self.server_links.append({"name": name, "href": href})


class _SeriesDetailParser(HTMLParser):
    """Parse kinoking.cc series detail page for seasons and episodes.

    Structure::

        <h1>Batman</h1>
        <div class="genre-badges">
          <span class="genre-badge">Action & Adventure</span>
        </div>
        <a href="?id=7515&season=1">Staffel 1 65 Episoden</a>
        <div class="content-card" onclick="playEpisode(242886, 'Title')">
          <h3>Episode Title</h3>
        </div>
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.genres: list[str] = []
        self.seasons: list[dict[str, str]] = []
        self.episodes: list[dict[str, str]] = []

        self._in_h1 = False
        self._h1_text = ""
        self._in_genre_span = False
        self._genre_text = ""
        self._in_episode_card = False
        self._episode_card_depth = 0
        self._episode_onclick = ""
        self._in_ep_h3 = False
        self._ep_h3_text = ""

    def _handle_div_start(
        self, classes: list[str], attr_dict: dict[str, str | None]
    ) -> None:
        if self._in_episode_card:
            self._episode_card_depth += 1
        elif "content-card" in classes:
            onclick = attr_dict.get("onclick", "") or ""
            m = re.match(r"playEpisode\((\d+)", onclick)
            if m:
                self._in_episode_card = True
                self._episode_card_depth = 0
                self._episode_onclick = onclick
                self._ep_h3_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class", "") or "").split()

        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        if tag == "span" and any(c.startswith("genre-") for c in classes):
            self._in_genre_span = True
            self._genre_text = ""

        # Season links: <a href="?id=7515&season=1">
        if tag == "a":
            href = attr_dict.get("href", "") or ""
            m = re.search(r"[?&]season=(\d+)", href)
            if m:
                season_num = m.group(1)
                if not any(s["number"] == season_num for s in self.seasons):
                    self.seasons.append({"number": season_num, "href": href})

        if tag == "div":
            self._handle_div_start(classes, attr_dict)

        if tag == "h3" and self._in_episode_card:
            self._in_ep_h3 = True
            self._ep_h3_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data
        if self._in_genre_span:
            self._genre_text += data
        if self._in_ep_h3:
            self._ep_h3_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = self._h1_text.strip()

        if tag == "span" and self._in_genre_span:
            self._in_genre_span = False
            genre = self._genre_text.strip()
            if genre and genre not in self.genres:
                self.genres.append(genre)

        if tag == "h3" and self._in_ep_h3:
            self._in_ep_h3 = False

        if tag == "div" and self._in_episode_card:
            if self._episode_card_depth > 0:
                self._episode_card_depth -= 1
            else:
                self._in_episode_card = False
                onclick = self._episode_onclick
                title = self._ep_h3_text.strip()
                m = re.match(r"playEpisode\((\d+)", onclick)
                if m:
                    self.episodes.append({"episode_id": m.group(1), "title": title})


class KinokingPlugin(HttpxPluginBase):
    """Python plugin for kinoking.cc using httpx.

    Scrapes movies and TV series with hoster links.
    """

    name = "kinoking"
    provides = "stream"
    _domains = _DOMAINS

    async def _search_cards(self, query: str) -> list[dict[str, str]]:
        """Fetch search page and return parsed content cards."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/index.php",
                params={"search": query},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("kinoking_search_failed", query=query, error=str(exc))
            return []

        parser = _SearchCardParser()
        parser.feed(resp.text)

        self._log.info(
            "kinoking_search",
            query=query,
            count=len(parser.results),
        )
        return parser.results

    async def _scrape_movie_detail(self, movie_id: str) -> _MovieDetailParser:
        """Fetch movie detail page and return parsed data."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/movie.php",
                params={"id": movie_id},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "kinoking_movie_detail_failed",
                movie_id=movie_id,
                error=str(exc),
            )
            return _MovieDetailParser()

        parser = _MovieDetailParser()
        parser.feed(resp.text)
        return parser

    async def _scrape_movie_link(self, movie_id: str, link_href: str) -> str:
        """Fetch a movie page with a specific link ID to get the iframe URL."""
        client = await self._ensure_client()

        url = f"{self.base_url}/movie.php"
        # href is like "?id=9456&link=9415"
        m = re.search(r"link=(\d+)", link_href)
        if not m:
            return ""

        try:
            resp = await client.get(url, params={"id": movie_id, "link": m.group(1)})
            resp.raise_for_status()
        except Exception:  # noqa: BLE001
            return ""

        parser = _MovieDetailParser()
        parser.feed(resp.text)
        return parser.iframe_src

    async def _scrape_series_detail(
        self, series_id: str, season: int | None = None
    ) -> _SeriesDetailParser:
        """Fetch series detail page and return parsed data."""
        client = await self._ensure_client()

        params: dict[str, str] = {"id": series_id}
        if season is not None:
            params["season"] = str(season)

        try:
            resp = await client.get(f"{self.base_url}/series.php", params=params)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "kinoking_series_detail_failed",
                series_id=series_id,
                error=str(exc),
            )
            return _SeriesDetailParser()

        parser = _SeriesDetailParser()
        parser.feed(resp.text)
        return parser

    async def _fetch_episode_links(self, episode_id: str) -> list[str]:
        """Call episode API to get hoster links."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/api/episode-navigation.php",
                params={"episode_id": episode_id},
            )
            resp.raise_for_status()
            data = json.loads(resp.text)
            links = data.get("links", [])
            return [str(lnk) for lnk in links if lnk]
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "kinoking_episode_api_failed",
                episode_id=episode_id,
                error=str(exc),
            )
            return []

    async def _build_movie_result(
        self,
        card: dict[str, str],
        category: int | None,
    ) -> SearchResult | None:
        """Scrape movie detail and build a SearchResult."""
        movie_id = card["id"]
        detail = await self._scrape_movie_detail(movie_id)

        if not detail.iframe_src:
            return None

        title = detail.title or card.get("title", "Unknown")
        genres = detail.genres
        torznab_cat = _determine_category(genres, False, category)

        # Build download_links from server links
        links: list[dict[str, str]] = [{"hoster": "primary", "link": detail.iframe_src}]

        # Resolve additional server links with bounded concurrency
        sem = self._new_semaphore()

        async def _resolve_link(
            srv: dict[str, str],
        ) -> dict[str, str] | None:
            async with sem:
                url = await self._scrape_movie_link(movie_id, srv["href"])
                if url and url != detail.iframe_src:
                    return {"hoster": srv["name"].lower(), "link": url}
                return None

        if detail.server_links:
            resolved = await asyncio.gather(
                *[_resolve_link(s) for s in detail.server_links],
                return_exceptions=True,
            )
            for r in resolved:
                if isinstance(r, dict):
                    links.append(r)

        source_url = f"{self.base_url}/movie.php?id={movie_id}"

        return SearchResult(
            title=title,
            download_link=detail.iframe_src,
            download_links=links,
            source_url=source_url,
            category=torznab_cat,
            metadata={
                "genres": ", ".join(genres),
                "rating": card.get("rating", ""),
            },
        )

    async def _build_series_results(
        self,
        card: dict[str, str],
        category: int | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Scrape series detail and build SearchResults per episode."""
        series_id = card["id"]

        # Fetch the requested season directly when specified
        detail = await self._scrape_series_detail(series_id, season=season)

        if not detail.title and not card.get("title"):
            return []

        series_title = detail.title or card.get("title", "Unknown")
        genres = detail.genres
        torznab_cat = _determine_category(genres, True, category)

        # If no seasons found, try default page episodes
        if not detail.seasons and not detail.episodes:
            return []

        # Use episodes from the loaded season
        episodes = detail.episodes
        if not episodes and detail.seasons:
            target = str(season) if season is not None else detail.seasons[0]["number"]
            season_detail = await self._scrape_series_detail(series_id, int(target))
            episodes = season_detail.episodes

        if not episodes:
            return []

        # Filter to a specific episode when requested
        if episode is not None:
            episodes = [ep for i, ep in enumerate(episodes, 1) if i == episode]

        # Fetch episode links with bounded concurrency
        sem = self._new_semaphore()

        async def _fetch_ep(
            ep: dict[str, str],
        ) -> SearchResult | None:
            async with sem:
                links = await self._fetch_episode_links(ep["episode_id"])
                if not links:
                    return None

                ep_title = ep.get("title", "")
                full_title = (
                    f"{series_title} - {ep_title}" if ep_title else series_title
                )

                dl_links = [{"hoster": "stream", "link": lnk} for lnk in links]

                return SearchResult(
                    title=full_title,
                    download_link=links[0],
                    download_links=dl_links,
                    source_url=(f"{self.base_url}/series.php?id={series_id}"),
                    category=torznab_cat,
                    metadata={
                        "series": series_title,
                        "episode_id": ep["episode_id"],
                        "genres": ", ".join(genres),
                    },
                )

        gathered = await asyncio.gather(
            *[_fetch_ep(ep) for ep in episodes],
            return_exceptions=True,
        )
        return [r for r in gathered if isinstance(r, SearchResult)]

    def _filter_cards(
        self,
        cards: list[dict[str, str]],
        category: int | None,
    ) -> list[dict[str, str]]:
        """Filter search cards by Torznab category."""
        if category is None:
            return cards
        if category < 5000:
            return [c for c in cards if c["type"] == "movie"]
        return [c for c in cards if c["type"] == "series"]

    async def _process_movies(
        self,
        movies: list[dict[str, str]],
        category: int | None,
    ) -> list[SearchResult]:
        """Process movie cards into SearchResults."""
        sem = self._new_semaphore()

        async def _bounded(card: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._build_movie_result(card, category)

        gathered = await asyncio.gather(
            *[_bounded(c) for c in movies],
            return_exceptions=True,
        )
        return [r for r in gathered if isinstance(r, SearchResult)]

    async def _process_series(
        self,
        series: list[dict[str, str]],
        category: int | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Process series cards into SearchResults."""
        sem = self._new_semaphore()

        async def _bounded(
            card: dict[str, str],
        ) -> list[SearchResult]:
            async with sem:
                return await self._build_series_results(
                    card, category, season=season, episode=episode
                )

        gathered = await asyncio.gather(
            *[_bounded(c) for c in series],
            return_exceptions=True,
        )
        results: list[SearchResult] = []
        for r in gathered:
            if isinstance(r, list):
                results.extend(r)
        return results

    async def _process_cards(
        self,
        cards: list[dict[str, str]],
        category: int | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Process search cards into SearchResults with concurrency."""
        filtered = self._filter_cards(cards, category)
        results: list[SearchResult] = []

        # Skip movies when season/episode are requested
        if season is None:
            movies = [c for c in filtered if c["type"] == "movie"]
            if movies:
                results.extend(await self._process_movies(movies, category))

        series = [c for c in filtered if c["type"] == "series"]
        if series:
            results.extend(
                await self._process_series(
                    series, category, season=season, episode=episode
                )
            )

        return results[: self._max_results]

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search kinoking.cc and return results with hoster links."""
        await self._ensure_client()
        await self._verify_domain()

        cards = await self._search_cards(query)
        if not cards:
            return []

        return await self._process_cards(
            cards, category, season=season, episode=episode
        )


plugin = KinokingPlugin()
