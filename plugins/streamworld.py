"""streamworld.ws Python plugin for Scavengarr.

Scrapes streamworld.ws (German streaming link aggregator) via Playwright:
- Playwright bypasses anti-bot JavaScript protection
- POST /suche.html for keyword search (all results on single page)
- Detail page scraping for release names and stream hoster links
- Category filtering: Film (Movies 2000) / Serie (TV 5000) from search results
- Genre-based sub-categorization (Action, Horror, Animation, etc.)
- Bounded concurrency for detail page scraping

Mirror domains: streamworld.ws, streamworld.co
Anti-bot JS protection requires browser-based access (Playwright mode).

Note: Site appears abandoned (latest content from 2022), small catalog.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from playwright.async_api import Page

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = [
    "streamworld.ws",
    "streamworld.co",
]
_NAV_TIMEOUT = 30_000
_ANTIBOT_TIMEOUT = 15_000  # ms to wait for anti-bot JS to finish

# Regex to extract SxxExx from release names.
_RELEASE_EPISODE_RE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,4})")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Torznab category -> search result type text.
# Search results have "Film" or "Serie" in the first cell.
_TYPE_TO_TORZNAB: dict[str, int] = {
    "film": 2000,
    "serie": 5000,
}

# Torznab category -> type filter for search results.
_TORZNAB_TO_TYPE: dict[int, str] = {
    2000: "film",
    2010: "film",
    2020: "film",
    2030: "film",
    2040: "film",
    2045: "film",
    2050: "film",
    2060: "film",
    5000: "serie",
    5010: "serie",
    5020: "serie",
    5030: "serie",
    5040: "serie",
    5050: "serie",
    5060: "serie",
    5070: "serie",
    5080: "serie",
}

# JavaScript to POST search form and return the response HTML.
# Uses FormData (multipart/form-data) because the server rejects
# application/x-www-form-urlencoded.
_SEARCH_FETCH_JS = """
async ([url, query]) => {
    const timeInput = document.querySelector('input[name="time"]');
    const fd = new FormData();
    fd.append('search', query);
    fd.append('time', timeInput ? timeInput.value : '0');
    const resp = await fetch(url, {
        method: 'POST',
        body: fd,
    });
    if (!resp.ok) return {_error: resp.status};
    return {html: await resp.text()};
}
"""

# JavaScript to GET a page and return the response HTML.
_GET_FETCH_JS = """
async (url) => {
    const resp = await fetch(url);
    if (!resp.ok) return {_error: resp.status};
    return {html: await resp.text()};
}
"""


def _find_matching_release(
    releases: list[dict[str, str]],
    season: int | None,
    episode: int | None,
) -> dict[str, str] | None:
    """Find the release whose name matches the requested season/episode."""
    for release in releases:
        m = _RELEASE_EPISODE_RE.search(release["name"])
        if not m:
            continue
        r_season = int(m.group(1))
        r_episode = int(m.group(2))
        if season is not None and r_season != season:
            continue
        if episode is not None and r_episode != episode:
            continue
        return release
    return None


class _SearchResultParser(HTMLParser):
    """Parse streamworld.ws search results table.

    Search results are in a ``<table>`` inside ``#content`` with rows::

        <tr>
          <td>Film</td>                          <!-- type -->
          <td><span class="otherLittles">
            <a href="/film/123-title.html">Title</a>
          </span></td>                           <!-- title + URL -->
          <td><img alt="Deutsch"></td>            <!-- language -->
          <td><span class="otherLittles">
            <a href="/jahr/2022.html">2022</a>
          </span></td>                           <!-- year -->
          <td>
            <span class="otherLittles">
              <a href="/genre/action.html">Action</a>
            </span>, ...
          </td>                                  <!-- genres -->
          <td><span class="otherLittles">7.6 / 10</span></td>  <!-- IMDB -->
        </tr>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

        # Row tracking
        self._in_table = False
        self._in_row = False
        self._cell_index = 0
        self._in_cell = False
        self._cell_depth = 0
        self._header_row = True

        # Current row data
        self._current_type = ""
        self._current_title = ""
        self._current_href = ""
        self._current_year = ""
        self._current_genres: list[str] = []
        self._current_imdb = ""

        # Link tracking
        self._in_a = False
        self._a_href = ""
        self._a_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "table" and not self._in_table:
            self._in_table = True
            return

        if not self._in_table:
            return

        if tag == "tr":
            self._in_row = True
            self._cell_index = 0
            self._current_type = ""
            self._current_title = ""
            self._current_href = ""
            self._current_year = ""
            self._current_genres = []
            self._current_imdb = ""

        if tag == "th":
            self._header_row = True

        if tag == "td" and self._in_row:
            self._in_cell = True
            self._cell_depth = 0
            self._cell_index += 1

        if tag == "a" and self._in_cell:
            self._in_a = True
            self._a_href = attr_dict.get("href", "") or ""
            self._a_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_a and self._in_cell:
            self._a_text += data
        elif self._in_cell and not self._in_a:
            text = data.strip()
            if text and self._cell_index == 1:
                self._current_type += text
            elif text and self._cell_index == 6:
                self._current_imdb += text

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            self._in_a = False
            text = self._a_text.strip()
            href = self._a_href

            if (
                self._cell_index == 2
                and href
                and ("/film/" in href or "/serie/" in href)
            ):
                self._current_title = text
                self._current_href = href

            elif self._cell_index == 4 and "/jahr/" in href:
                self._current_year = text

            elif self._cell_index == 5 and "/genre/" in href:
                self._current_genres.append(text)

        if tag == "td":
            self._in_cell = False

        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._header_row:
                self._header_row = False
                return

            if self._current_title and self._current_href:
                self.results.append(
                    {
                        "type": self._current_type.strip().lower(),
                        "title": self._current_title,
                        "url": urljoin(self._base_url, self._current_href),
                        "year": self._current_year,
                        "genres": ", ".join(self._current_genres),
                        "imdb": self._current_imdb.strip(),
                    }
                )

        if tag == "table" and self._in_table:
            self._in_table = False


class _DetailPageParser(HTMLParser):
    """Parse streamworld.ws film/series detail page.

    Extracts release names and stream page URLs from::

        <table>
          <tr>
            <th><a href="/film/{id}-{slug}/streams-{sid}.html">
              Release.Name.Here
            </a></th>
          </tr>
          <tr>
            <td>
              Verfuegbare Streams
              <a href="..."><img alt="streamtape.com"></a>
              ...
            </td>
          </tr>
        </table>

    Also extracts individual hoster stream links from the streams sub-page::

        <table>
          <tr>
            <td><strong>streamtape.com</strong></td>
            <td></td>
            <td><a href="/film/{sid}-{title}/stream/{lid}-{hoster}.html">
              <span>1</span>
            </a></td>
          </tr>
        </table>
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url
        self.releases: list[dict[str, str]] = []
        self.stream_links: list[dict[str, str]] = []
        self.description = ""
        self.imdb_url = ""

        # Release header tracking
        self._in_th = False
        self._in_th_a = False
        self._th_a_href = ""
        self._th_a_text = ""

        # Stream link tracking (on streams sub-page)
        # Always track <strong> text so we capture hoster names
        # that appear before the first /stream/ link.
        self._in_strong = False
        self._current_hoster = ""
        self._in_stream_a = False
        self._stream_a_href = ""

        # Description tracking
        self._in_desc_div = False
        self._desc_text = ""

        # IMDB link
        self._in_imdb_a = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        href = attr_dict.get("href", "") or ""

        if tag == "th":
            self._in_th = True

        if tag == "a" and self._in_th and "/streams-" in href:
            self._in_th_a = True
            self._th_a_href = href
            self._th_a_text = ""

        # IMDB link
        if tag == "a" and "imdb.com" in href:
            self.imdb_url = href

        # Hoster name in <strong> (always track, as it precedes /stream/ links)
        if tag == "strong":
            self._in_strong = True
            self._current_hoster = ""

        # Stream link to individual page
        if tag == "a" and "/stream/" in href:
            self._in_stream_a = True
            self._stream_a_href = href

    def handle_data(self, data: str) -> None:
        if self._in_th_a:
            self._th_a_text += data
        if self._in_strong:
            self._current_hoster += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "th":
            self._in_th = False

        if tag == "a" and self._in_th_a:
            self._in_th_a = False
            name = self._th_a_text.strip()
            href = self._th_a_href
            if name and href:
                # Clean the arrow prefix
                name = re.sub(r"^[▶▼]\s*", "", name).strip()
                self.releases.append(
                    {
                        "name": name,
                        "streams_url": urljoin(self._base_url, href),
                    }
                )

        if tag == "a" and self._in_stream_a:
            self._in_stream_a = False
            href = self._stream_a_href
            if href and self._current_hoster:
                hoster = self._current_hoster.strip()
                # Skip usenet/premium promo links
                if "usenet" not in hoster.lower():
                    self.stream_links.append(
                        {
                            "hoster": hoster.split(".")[0].lower(),
                            "link": urljoin(self._base_url, href),
                        }
                    )
                self._current_hoster = ""

        if tag == "strong":
            self._in_strong = False


class StreamworldPlugin(PlaywrightPluginBase):
    """Python plugin for streamworld.ws using Playwright (anti-bot bypass)."""

    name = "streamworld"
    version = "1.1.0"
    mode = "playwright"
    provides = "stream"
    default_language = "de"

    _domains = _DOMAINS
    _serialize_search = True

    async def _wait_for_antibot(self, page: "Page") -> bool:
        """Wait for anti-bot JavaScript to finish on the homepage.

        The site uses obfuscated JS that must execute before requests
        succeed.  We detect completion by checking for PHPSESSID cookie
        and actual page content (``#content`` or a ``<table>``).
        """
        try:
            await page.wait_for_selector(
                "#content, table, .topnav, nav",
                timeout=_ANTIBOT_TIMEOUT,
            )
            return True
        except Exception:  # noqa: BLE001
            # Check if PHPSESSID cookie was set (anti-bot resolved)
            cookies = await page.context.cookies()
            for c in cookies:
                if c["name"] == "PHPSESSID":
                    return True
            self._log.warning("streamworld_antibot_timeout")
            return False

    async def _verify_domain(self) -> None:
        """Navigate to a working domain and execute anti-bot JS."""
        if self._domain_verified:
            return

        page = await self._ensure_page()

        for domain in self._domains:
            url = f"https://{domain}/"
            try:
                await page.goto(url, wait_until="networkidle")
                if await self._wait_for_antibot(page):
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    self._log.info("streamworld_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{self._domains[0]}"
        self._domain_verified = True
        self._log.warning("streamworld_no_domain_reachable", fallback=self._domains[0])

    async def _fetch_html(self, url: str) -> str | None:
        """Fetch a page's HTML using the browser context via fetch()."""
        page = await self._ensure_page()

        try:
            data = await page.evaluate(_GET_FETCH_JS, url)
        except Exception as exc:  # noqa: BLE001
            self._log.warning("streamworld_fetch_failed", url=url, error=str(exc))
            return None

        if not isinstance(data, dict):
            return None
        if "_error" in data:
            self._log.warning("streamworld_fetch_error", url=url, status=data["_error"])
            return None
        return data.get("html")

    async def _search_page(self, query: str) -> list[dict[str, str]]:
        """Submit search form via Playwright and parse results.

        All results appear on a single page (no pagination).
        """
        page = await self._ensure_page()

        url = f"{self.base_url}/suche.html"

        try:
            data = await page.evaluate(_SEARCH_FETCH_JS, [url, query])
        except Exception as exc:  # noqa: BLE001
            self._log.warning("streamworld_search_failed", query=query, error=str(exc))
            return []

        if not isinstance(data, dict) or "_error" in data:
            status = data.get("_error", "unknown") if isinstance(data, dict) else "?"
            self._log.warning("streamworld_search_error", query=query, status=status)
            return []

        html = data.get("html", "")
        if not html:
            return []

        parser = _SearchResultParser(self.base_url)
        parser.feed(html)

        self._log.info(
            "streamworld_search_results",
            query=query,
            results=len(parser.results),
        )
        return parser.results

    async def _scrape_detail(
        self,
        result: dict[str, str],
        season: int | None = None,
        episode: int | None = None,
    ) -> SearchResult | None:
        """Scrape a film/series detail page for stream links."""
        detail_url = result["url"]
        html = await self._fetch_html(detail_url)
        if not html:
            return None

        parser = _DetailPageParser(self.base_url)
        parser.feed(html)

        if not parser.releases:
            self._log.debug("streamworld_no_releases", url=detail_url)
            return None

        # When season/episode requested, find the matching release
        target_release = None
        if season is not None or episode is not None:
            target_release = _find_matching_release(parser.releases, season, episode)
            if target_release is None:
                self._log.debug(
                    "streamworld_no_matching_release",
                    url=detail_url,
                    season=season,
                    episode=episode,
                    releases=[r["name"] for r in parser.releases[:5]],
                )
                # Fall through to first release; central filter handles later

        selected_release = target_release or parser.releases[0]
        streams_url = selected_release["streams_url"]

        # Fetch the streams sub-page to get individual hoster links
        download_links = await self._scrape_streams_page(streams_url)

        if not download_links:
            # Fallback: use the streams page URL itself
            download_links = [{"hoster": "streamworld", "link": streams_url}]

        # Determine category from type
        result_type = result.get("type", "film")
        category = _TYPE_TO_TORZNAB.get(result_type, 2000)

        # Build metadata
        year = result.get("year", "")
        genres = result.get("genres", "")
        imdb_text = result.get("imdb", "")

        return SearchResult(
            title=result["title"],
            download_link=download_links[0]["link"],
            download_links=download_links,
            source_url=detail_url,
            category=category,
            release_name=selected_release["name"],
            description=f"{genres} ({year})" if genres and year else genres or year,
            metadata={
                "year": year,
                "genres": genres,
                "imdb": imdb_text,
                "imdb_url": parser.imdb_url,
                "releases": [r["name"] for r in parser.releases],
            },
        )

    async def _scrape_streams_page(self, streams_url: str) -> list[dict[str, str]]:
        """Scrape a streams sub-page for individual hoster links."""
        html = await self._fetch_html(streams_url)
        if not html:
            return []

        parser = _DetailPageParser(self.base_url)
        parser.feed(html)
        return parser.stream_links

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search streamworld.ws and return results with stream links."""
        await self._verify_domain()

        all_results = await self._search_page(query)

        if not all_results:
            return []

        # When season is requested, restrict to series type
        effective_category = category
        if season is not None and effective_category is None:
            effective_category = 5000

        # Filter by type if Torznab category is specified
        if effective_category is not None:
            type_filter = _TORZNAB_TO_TYPE.get(effective_category)
            if type_filter:
                all_results = [
                    r for r in all_results if r.get("type", "") == type_filter
                ]

        all_results = all_results[: self.effective_max_results]

        if not all_results:
            return []

        # Scrape detail pages with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded_scrape(r: dict[str, str]) -> SearchResult | None:
            async with sem:
                return await self._scrape_detail(r, season=season, episode=episode)

        results = await asyncio.gather(
            *[_bounded_scrape(r) for r in all_results],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, SearchResult)]


plugin = StreamworldPlugin()
