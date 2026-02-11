"""s.to (SerienStream) Python plugin for Scavengarr.

Scrapes s.to (German TV series streaming site) with:
- httpx for all requests (server-rendered HTML, no JS challenges)
- Search via /suche?term={query} with pagination (&page=N)
- Series detail pages at /serie/{slug} for seasons/episodes
- Episode pages at /serie/{slug}/staffel-{n}/episode-{n} for hoster buttons
- Hoster redirect resolution via /r?t={token} → 302 to actual hoster URL
- Bounded concurrency for series and episode detail scraping

Multi-domain support with automatic fallback (s.to, serienstream.to, 186.2.175.5).
TV-only site: all results use Torznab category 5000+.
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

# Known domains in priority order.
_DOMAINS = [
    "s.to",
    "serienstream.to",
    "186.2.175.5",
]

_BASE_URL = f"https://{_DOMAINS[0]}"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_CONCURRENT_EPISODES = 3
_MAX_RESULTS = 1000
_MAX_PAGES = 42  # 24 results/page → 42 pages for ~1000
_RESULTS_PER_PAGE = 24

# Site genre name (lowercase) → Torznab TV sub-category.
_GENRE_CATEGORY_MAP: dict[str, int] = {
    "anime": 5070,
    "animation": 5070,
    "zeichentrick": 5070,
    "comedy": 5030,
    "drama": 5030,
    "horror": 5040,
    "thriller": 5040,
    "mystery": 5040,
    "action": 5030,
    "science-fiction": 5030,
    "science fiction": 5030,
    "fantasy": 5050,
    "dokumentation": 5080,
    "documentary": 5080,
    "doku-soap": 5080,
    "kinderserie": 5040,
}


def _genre_to_torznab(genre: str) -> int:
    """Map s.to genre name to Torznab TV sub-category."""
    key = genre.lower().strip()
    return _GENRE_CATEGORY_MAP.get(key, 5000)


def _determine_category(genres: list[str], category: int | None) -> int:
    """Determine Torznab category from genres, with caller override."""
    if category is not None:
        return category
    for genre in genres:
        mapped = _genre_to_torznab(genre)
        if mapped != 5000:
            return mapped
    return 5000


class _SearchSeriesParser(HTMLParser):
    """Parse s.to search results page for series entries.

    Search results are under an ``<h2>Serien</h2>`` heading, followed by
    a container with series cards. Each card has::

        <a href="/serie/{slug}">
          ...
          <h6>Series Title</h6>
          ...
        </a>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

        # State tracking
        self._found_serien_heading = False
        self._in_series_a = False
        self._in_h6 = False
        self._current_href = ""
        self._current_title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        href = attr_dict.get("href", "") or ""

        if tag == "a" and "/serie/" in href:
            self._in_series_a = True
            self._current_href = href
            self._current_title = ""

        if tag == "h6" and self._in_series_a:
            self._in_h6 = True
            self._current_title = ""

    def handle_data(self, data: str) -> None:
        text = data.strip()

        # Detect the "Serien" heading
        if text.lower() == "serien":
            self._found_serien_heading = True

        if self._in_h6:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h6" and self._in_h6:
            self._in_h6 = False

        if tag == "a" and self._in_series_a:
            self._in_series_a = False
            title = self._current_title.strip()
            href = self._current_href

            if title and href:
                # Extract slug from href: /serie/{slug}
                slug = href.rstrip("/").split("/")[-1] if "/serie/" in href else ""
                self.results.append(
                    {
                        "title": title,
                        "url": urljoin(self._base_url, href),
                        "slug": slug,
                    }
                )


class _SeriesDetailParser(HTMLParser):
    """Parse s.to series detail page for genres, seasons, and episodes.

    Page structure:
    - ``<h1>Series Title</h1>``
    - Genre links: ``<a href="/genre/{name}">Genre</a>``
    - Season nav: ``<a href="/serie/{slug}/staffel-{n}">Staffel {n}</a>``
    - Episode table with rows containing:
      - ``<th>`` with episode number
      - ``<strong>`` with German title
      - ``<div>`` with English title (after ``<span>`` with DE title)
      - ``<img alt="Hoster">`` for hoster icons
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

        # Metadata
        self.title = ""
        self.genres: list[str] = []
        self.seasons: list[int] = []

        # Episode data for the currently displayed season
        self.episodes: list[dict[str, str]] = []

        # State tracking
        self._in_h1 = False
        self._h1_text = ""
        self._in_genre_a = False
        self._genre_a_text = ""
        self._in_season_a = False
        self._season_a_href = ""

        # Episode table tracking
        self._in_episode_tr = False
        self._in_th = False
        self._th_text = ""
        self._in_strong = False
        self._strong_text = ""
        self._episode_number = ""
        self._episode_de_title = ""
        self._episode_en_title = ""
        self._episode_hosters: list[str] = []
        self._in_episode_td = False
        self._episode_td_count = 0

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        href = attr_dict.get("href", "") or ""

        if tag == "h1":
            self._in_h1 = True
            self._h1_text = ""

        # Genre links: <a href="/genre/{name}">
        if tag == "a" and "/genre/" in href:
            self._in_genre_a = True
            self._genre_a_text = ""

        # Season navigation links: <a href="/serie/{slug}/staffel-{n}">
        if tag == "a" and "/staffel-" in href:
            self._in_season_a = True
            self._season_a_href = href
            m = re.search(r"/staffel-(\d+)", href)
            if m:
                season_num = int(m.group(1))
                if season_num not in self.seasons:
                    self.seasons.append(season_num)

        # Episode table row
        if tag == "tr":
            self._in_episode_tr = True
            self._episode_number = ""
            self._episode_de_title = ""
            self._episode_en_title = ""
            self._episode_hosters = []
            self._episode_td_count = 0

        if tag == "th" and self._in_episode_tr:
            self._in_th = True
            self._th_text = ""

        if tag == "td" and self._in_episode_tr:
            self._in_episode_td = True
            self._episode_td_count += 1

        if tag == "strong" and self._in_episode_tr:
            self._in_strong = True
            self._strong_text = ""

        # Hoster icons: <img alt="VOE"> within episode row
        if tag == "img" and self._in_episode_tr:
            alt = attr_dict.get("alt", "") or ""
            if alt and alt.lower() not in {"", "flag", "poster", "cover"}:
                self._episode_hosters.append(alt)

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._h1_text += data

        if self._in_genre_a:
            self._genre_a_text += data

        if self._in_th:
            self._th_text += data

        if self._in_strong and self._in_episode_tr:
            self._strong_text += data

    def _handle_a_end(self) -> None:
        if self._in_genre_a:
            self._in_genre_a = False
            genre = self._genre_a_text.strip()
            if genre and genre not in self.genres:
                self.genres.append(genre)
        if self._in_season_a:
            self._in_season_a = False

    def _handle_tr_end(self) -> None:
        self._in_episode_tr = False
        if self._episode_number:
            self.episodes.append(
                {
                    "number": self._episode_number,
                    "de_title": self._episode_de_title,
                    "en_title": self._episode_en_title,
                    "hosters": ",".join(self._episode_hosters),
                }
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = self._h1_text.strip()

        if tag == "a":
            self._handle_a_end()

        if tag == "th" and self._in_th:
            self._in_th = False
            text = self._th_text.strip()
            if re.match(r"^\d+$", text):
                self._episode_number = text

        if tag == "strong" and self._in_strong:
            self._in_strong = False
            text = self._strong_text.strip()
            if text and self._in_episode_tr and not self._episode_de_title:
                self._episode_de_title = text

        if tag == "td" and self._in_episode_td:
            self._in_episode_td = False

        if tag == "tr" and self._in_episode_tr:
            self._handle_tr_end()


class _EpisodeHosterParser(HTMLParser):
    """Parse s.to episode page for hoster buttons.

    Episode pages contain hoster buttons grouped by language::

        <h5>Deutsch</h5>
        <button class="link-box ..."
                data-play-url="/r?t={token}"
                data-provider-name="VOE"
                data-language-label="Deutsch"
                data-language-id="1">
          ...
        </button>
    """

    def __init__(self) -> None:
        super().__init__()
        self.hosters: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"button", "a"}:
            return

        attr_dict = dict(attrs)
        play_url = attr_dict.get("data-play-url", "") or ""
        provider = attr_dict.get("data-provider-name", "") or ""
        language = attr_dict.get("data-language-label", "") or ""

        if play_url and provider:
            self.hosters.append(
                {
                    "play_url": play_url,
                    "provider": provider,
                    "language": language,
                }
            )


class StoPlugin:
    """Python plugin for s.to (SerienStream) using httpx.

    Supports multiple domains with automatic fallback:
    s.to, serienstream.to, 186.2.175.5.
    """

    name = "sto"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._domain_verified = False
        self.base_url = _BASE_URL

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create httpx client if not already running."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def _verify_domain(self) -> None:
        """Find and cache a working domain from the fallback list."""
        if self._domain_verified:
            return

        client = await self._ensure_client()
        for domain in _DOMAINS:
            url = f"https://{domain}/"
            try:
                resp = await client.head(url, timeout=5.0)
                if resp.status_code == 200:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    log.info("sto_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("sto_no_domain_reachable", fallback=_DOMAINS[0])

    async def _search_series(
        self,
        query: str,
        page_num: int = 1,
    ) -> list[dict[str, str]]:
        """Fetch one search page and return parsed series entries."""
        client = await self._ensure_client()

        params: dict[str, str | int] = {"term": query}
        if page_num > 1:
            params["page"] = page_num

        try:
            resp = await client.get(
                f"{self.base_url}/suche",
                params=params,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sto_search_failed",
                query=query,
                page=page_num,
                error=str(exc),
            )
            return []

        parser = _SearchSeriesParser(self.base_url)
        parser.feed(resp.text)

        log.info(
            "sto_search_page",
            query=query,
            page=page_num,
            count=len(parser.results),
        )
        return parser.results

    async def _scrape_series_detail(
        self,
        series: dict[str, str],
    ) -> _SeriesDetailParser:
        """Fetch series detail page and return parsed data."""
        client = await self._ensure_client()

        try:
            resp = await client.get(series["url"])
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sto_series_detail_failed",
                url=series.get("url", ""),
                error=str(exc),
            )
            return _SeriesDetailParser(self.base_url)

        parser = _SeriesDetailParser(self.base_url)
        parser.feed(resp.text)
        return parser

    async def _scrape_episode_hosters(
        self,
        episode_url: str,
    ) -> list[dict[str, str]]:
        """Fetch episode page and return hoster button data."""
        client = await self._ensure_client()

        try:
            resp = await client.get(episode_url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sto_episode_failed",
                url=episode_url,
                error=str(exc),
            )
            return []

        parser = _EpisodeHosterParser()
        parser.feed(resp.text)
        return parser.hosters

    async def _resolve_hoster_url(self, play_url: str) -> str:
        """Resolve /r?t={token} redirect to actual hoster URL.

        Uses HEAD with follow_redirects=False to capture the 302 Location.
        Falls back to returning the original URL on failure.
        """
        client = await self._ensure_client()

        full_url = urljoin(self.base_url, play_url)
        try:
            resp = await client.head(
                full_url,
                follow_redirects=False,
                timeout=10.0,
            )
            location = resp.headers.get("location", "")
            if location and location.startswith("http"):
                return location
        except Exception:  # noqa: BLE001
            log.debug("sto_resolve_failed", url=full_url)

        return full_url

    async def _scrape_season_episodes(
        self,
        slug: str,
        season_num: int,
        detail: _SeriesDetailParser,
    ) -> list[dict[str, str | list[dict[str, str]]]]:
        """Scrape all episodes in a season for hoster links.

        Returns one entry per episode with title and resolved hoster URLs.
        """
        sem = asyncio.Semaphore(_MAX_CONCURRENT_EPISODES)

        # Build episode URLs from the detail parser's episode list
        episode_urls: list[tuple[str, str]] = []
        for ep in detail.episodes:
            ep_num = ep["number"]
            ep_title = ep["de_title"] or ep["en_title"]
            url = f"{self.base_url}/serie/{slug}/staffel-{season_num}/episode-{ep_num}"
            episode_urls.append((url, ep_title))

        if not episode_urls:
            return []

        async def _fetch_episode(
            ep_url: str,
            ep_title: str,
        ) -> dict[str, str | list[dict[str, str]]] | None:
            async with sem:
                hosters = await self._scrape_episode_hosters(ep_url)
                if not hosters:
                    return None

                # Resolve hoster URLs
                links: list[dict[str, str]] = []
                for h in hosters:
                    resolved = await self._resolve_hoster_url(h["play_url"])
                    links.append(
                        {
                            "hoster": h["provider"].lower(),
                            "link": resolved,
                            "language": h.get("language", ""),
                        }
                    )

                if not links:
                    return None

                return {
                    "title": ep_title,
                    "url": ep_url,
                    "links": links,
                }

        gathered = await asyncio.gather(
            *[_fetch_episode(url, title) for url, title in episode_urls],
            return_exceptions=True,
        )

        results: list[dict[str, str | list[dict[str, str]]]] = []
        for item in gathered:
            if isinstance(item, dict):
                results.append(item)

        return results

    async def _paginate_search(self, query: str) -> list[dict[str, str]]:
        """Paginate through search pages to collect series entries."""
        all_series: list[dict[str, str]] = []
        for page_num in range(1, _MAX_PAGES + 1):
            series = await self._search_series(query, page_num)
            if not series:
                break
            all_series.extend(series)
            if len(series) < _RESULTS_PER_PAGE:
                break
            if len(all_series) >= _MAX_RESULTS:
                break
        return all_series[:_MAX_RESULTS]

    async def _fetch_all_details(
        self,
        all_series: list[dict[str, str]],
    ) -> list[tuple[dict[str, str], _SeriesDetailParser]]:
        """Scrape series detail pages with bounded concurrency."""
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)

        async def _bounded(
            s: dict[str, str],
        ) -> tuple[dict[str, str], _SeriesDetailParser]:
            async with sem:
                detail = await self._scrape_series_detail(s)
                return s, detail

        gathered = await asyncio.gather(
            *[_bounded(s) for s in all_series],
            return_exceptions=True,
        )
        return [item for item in gathered if isinstance(item, tuple)]

    def _build_episode_result(
        self,
        ep: dict[str, str | list[dict[str, str]]],
        detail: _SeriesDetailParser,
        season: int,
        torznab_cat: int,
    ) -> SearchResult | None:
        """Convert a scraped episode dict into a SearchResult."""
        ep_title = str(ep.get("title", ""))
        ep_url = str(ep.get("url", ""))
        ep_links = ep.get("links", [])

        if not ep_links or not isinstance(ep_links, list):
            return None

        ep_num_match = re.search(r"/episode-(\d+)", ep_url)
        ep_num = ep_num_match.group(1) if ep_num_match else "0"
        full_title = f"{detail.title} - S{season:02d}E{int(ep_num):02d}"
        if ep_title:
            full_title += f" - {ep_title}"

        first_link = ep_links[0]
        download_link = (
            first_link["link"] if isinstance(first_link, dict) else str(first_link)
        )

        return SearchResult(
            title=full_title,
            download_link=download_link,
            download_links=ep_links,
            source_url=ep_url,
            category=torznab_cat,
            metadata={
                "series": detail.title,
                "season": str(season),
                "episode": ep_num,
                "genres": ", ".join(detail.genres),
            },
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search s.to and return results with hoster links.

        Each episode becomes one SearchResult. Only TV categories are
        returned since s.to is a TV-only site.
        """
        await self._ensure_client()
        await self._verify_domain()

        all_series = await self._paginate_search(query)
        if not all_series:
            return []

        detail_results = await self._fetch_all_details(all_series)

        search_results: list[SearchResult] = []
        for series_info, detail in detail_results:
            if not detail.seasons:
                continue

            slug = series_info.get("slug", "")
            if not slug:
                continue

            torznab_cat = _determine_category(detail.genres, category)
            first_season = detail.seasons[0]
            episodes = await self._scrape_season_episodes(slug, first_season, detail)

            for ep in episodes:
                result = self._build_episode_result(
                    ep, detail, first_season, torznab_cat
                )
                if result:
                    search_results.append(result)

            if len(search_results) >= _MAX_RESULTS:
                break

        return search_results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = StoPlugin()
