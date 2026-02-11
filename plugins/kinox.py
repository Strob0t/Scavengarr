"""kinox.to Python plugin for Scavengarr.

Scrapes kinox.to / kinos.to / kinoz.to (German streaming aggregator) with:
- httpx for all requests (server-rendered pages)
- Search: GET /Search.html?q={query}
- Detail pages at /Stream/{slug}.html with streaming hoster info
- Movies, TV Series, and Documentaries

Multi-domain support with automatic fallback.
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

# Known domains in priority order (all serve identical content)
_DOMAINS = [
    "www22.kinox.to",
    "ww22.kinox.to",
    "www22.kinos.to",
    "ww22.kinos.to",
    "www22.kinoz.to",
    "ww22.kinoz.to",
    "www20.kinox.to",
    "www15.kinox.to",
    "www.kinox.to",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000


class _SearchResultParser(HTMLParser):
    """Parse search results from a kinox.to search page.

    Each result is a ``<div onclick="location.href='/Stream/...'"``> with:
    - ``<a href="/Stream/{slug}.html"><h1>Title</h1></a>``
    - ``<div class="Genre">`` with genre links and IMDb rating
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []

        # Card tracking
        self._in_card = False
        self._card_div_depth = 0
        self._current_url = ""
        self._current_title = ""
        self._genre_parts: list[str] = []
        self._current_imdb = ""

        # State flags
        self._in_h1 = False
        self._in_genre_link = False

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)

        if tag == "div":
            if self._in_card:
                self._card_div_depth += 1
            else:
                onclick = attr_dict.get("onclick", "") or ""
                m = re.search(r"/Stream/[^'\"]+", onclick)
                if m:
                    self._in_card = True
                    self._card_div_depth = 0
                    self._current_url = m.group(0)
                    self._current_title = ""
                    self._genre_parts = []
                    self._current_imdb = ""

        if not self._in_card:
            return

        if tag == "h1":
            self._in_h1 = True
            self._current_title = ""

        if tag == "a":
            href = attr_dict.get("href", "") or ""
            if "/Genre/" in href:
                self._in_genre_link = True

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            self._current_title += data

        if self._in_genre_link:
            self._genre_parts.append(data.strip())

        if self._in_card and "/ 10" in data:
            self._current_imdb = data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self._in_h1 = False

        if tag == "a" and self._in_genre_link:
            self._in_genre_link = False

        if tag == "div" and self._in_card:
            if self._card_div_depth > 0:
                self._card_div_depth -= 1
            else:
                self._in_card = False
                if self._current_title.strip() and self._current_url:
                    self.results.append(
                        {
                            "title": self._current_title.strip(),
                            "url": self._current_url,
                            "genre": ", ".join(self._genre_parts),
                            "imdb": self._current_imdb,
                        }
                    )


class _DetailPageParser(HTMLParser):
    """Parse a kinox.to movie/series detail page.

    Extracts:
    - Title from ``<h1><span>Title</span> <span class="Year">(YYYY)</span></h1>``
    - Year from ``<span class="Year">``
    - Hosters from ``<ul id="HosterList"><li id="Hoster_N">``
    - Series detection via ``<select id="SeasonSelection">``
    """

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.year = ""
        self.hosters: list[dict[str, str]] = []
        self.is_series = False

        # h1 / year tracking
        self._in_h1 = False
        self._in_year_span = False
        self._h1_text = ""

        # Hoster tracking
        self._in_hoster_list = False
        self._in_hoster_item = False
        self._in_named_div = False
        self._current_hoster_name = ""
        self._current_hoster_id = ""

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        if tag == "h1" and not self.title:
            self._in_h1 = True
            self._h1_text = ""

        if tag == "span" and "Year" in classes:
            self._in_year_span = True

        if tag == "ul" and attr_dict.get("id") == "HosterList":
            self._in_hoster_list = True

        if tag == "li" and self._in_hoster_list:
            li_id = attr_dict.get("id", "") or ""
            if li_id.startswith("Hoster_"):
                self._in_hoster_item = True
                self._current_hoster_id = li_id.replace("Hoster_", "")
                self._current_hoster_name = ""

        if tag == "div" and "Named" in classes and self._in_hoster_item:
            self._in_named_div = True

        if tag == "select" and attr_dict.get("id") == "SeasonSelection":
            self.is_series = True

    def handle_data(self, data: str) -> None:
        if self._in_year_span:
            m = re.search(r"\d{4}", data)
            if m:
                self.year = m.group(0)

        if self._in_h1 and not self._in_year_span:
            self._h1_text += data

        if self._in_named_div:
            self._current_hoster_name += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._in_year_span:
            self._in_year_span = False

        if tag == "h1" and self._in_h1:
            self._in_h1 = False
            if self.year and not self.title:
                self.title = self._h1_text.strip()

        if tag == "ul" and self._in_hoster_list:
            self._in_hoster_list = False

        if tag == "li" and self._in_hoster_item:
            self._in_hoster_item = False
            name = self._current_hoster_name.strip()
            if name:
                self.hosters.append({"name": name, "id": self._current_hoster_id})

        if tag == "div" and self._in_named_div:
            self._in_named_div = False


class KinoxPlugin:
    """Python plugin for kinox.to / kinos.to / kinoz.to using httpx."""

    name = "kinox"
    version = "1.0.0"
    mode = "httpx"
    provides = "stream"
    default_language = "de"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self.base_url: str = f"https://{_DOMAINS[0]}"
        self._domain_verified = False

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
                    log.info("kinox_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        self.base_url = f"https://{_DOMAINS[0]}"
        self._domain_verified = True
        log.warning("kinox_no_domain_reachable", fallback=_DOMAINS[0])

    async def _search_page(self, query: str) -> list[dict[str, str]]:
        """Fetch search page and parse results."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/Search.html",
                params={"q": query},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("kinox_search_failed", query=query, error=str(exc))
            return []

        parser = _SearchResultParser()
        parser.feed(resp.text)

        log.info("kinox_search", query=query, count=len(parser.results))
        return parser.results

    async def _fetch_detail_page(self, url_path: str) -> _DetailPageParser:
        """Fetch a movie/series detail page and parse it."""
        client = await self._ensure_client()

        try:
            resp = await client.get(f"{self.base_url}{url_path}")
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("kinox_detail_failed", url=url_path, error=str(exc))
            return _DetailPageParser()

        parser = _DetailPageParser()
        parser.feed(resp.text)

        log.info(
            "kinox_detail",
            url=url_path,
            title=parser.title,
            year=parser.year,
            hosters=len(parser.hosters),
            is_series=parser.is_series,
        )
        return parser

    def _build_search_result(
        self,
        search_entry: dict[str, str],
        detail: _DetailPageParser,
    ) -> SearchResult:
        """Build a SearchResult from search entry and detail page data."""
        title = detail.title or search_entry.get("title", "")
        year = detail.year
        url_path = search_entry.get("url", "")
        source_url = f"{self.base_url}{url_path}"

        display_title = f"{title} ({year})" if year else title
        category = 5000 if detail.is_series else 2000

        return SearchResult(
            title=display_title,
            download_link=source_url,
            source_url=source_url,
            published_date=year or None,
            category=category,
        )

    async def _process_entry(
        self,
        entry: dict[str, str],
        sem: asyncio.Semaphore,
        category: int | None,
    ) -> SearchResult | None:
        """Fetch detail page for one search entry and build result."""
        url_path = entry.get("url", "")
        if not url_path:
            return None

        async with sem:
            detail = await self._fetch_detail_page(url_path)

        sr = self._build_search_result(entry, detail)

        # Post-filter by category range
        if category is not None:
            cat_range = (category // 1000) * 1000
            if not (cat_range <= sr.category < cat_range + 1000):
                return None

        return sr

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search kinox.to and return results.

        Uses the search page to find movies/series, then fetches detail
        pages to extract year, hosters, and content type.
        """
        if not query:
            return []

        # Accept movies (2xxx), TV (5xxx)
        if category is not None:
            if not (2000 <= category < 3000 or 5000 <= category < 6000):
                return []

        await self._ensure_client()
        await self._verify_domain()

        search_entries = await self._search_page(query)
        if not search_entries:
            return []

        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        tasks = [self._process_entry(e, sem, category) for e in search_entries]
        task_results = await asyncio.gather(*tasks)

        results: list[SearchResult] = []
        for sr in task_results:
            if sr is not None:
                results.append(sr)
                if len(results) >= _MAX_RESULTS:
                    break

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


plugin = KinoxPlugin()
