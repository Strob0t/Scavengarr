"""filmfans.org Python plugin for Scavengarr.

Scrapes filmfans.org (German movie DDL site) with:
- httpx for all requests (no Cloudflare challenge)
- JSON search API: GET /api/v2/search?q={query}&ql=DE
- Server-rendered movie pages at /{url_id} with all releases
- Download links via /external/{hash} redirect URLs
- Movies only (category 2000)

No authentication required.
"""

from __future__ import annotations

import asyncio
import json
import time
from html.parser import HTMLParser

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

_BASE_URL = "https://filmfans.org"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_MAX_CONCURRENT_DETAIL = 3
_MAX_RESULTS = 1000


class _ReleaseParser(HTMLParser):
    """Parse release entries from a filmfans.org movie page.

    Each release is a ``<div class="entry">`` containing:
    - ``<span class="morespec">Release.Name.Here</span>`` (release/scene name)
    - ``<span class="audiotag"><small>Größe:</small> 37.3 GB</span>`` (size)
    - ``<a class="dlb row" href="/external/{hash}?_={ts}">``
      ``<div class="col"><span>hoster_name</span></div></a>`` (download links)
    """

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.releases: list[dict[str, str | list[dict[str, str]]]] = []
        self._base_url = base_url

        # Entry tracking
        self._in_entry = False
        self._entry_div_depth = 0

        # Release name
        self._in_morespec = False
        self._current_release_name = ""

        # Audiotag (size, resolution)
        self._in_audiotag = False
        self._in_small = False
        self._small_text = ""
        self._audiotag_text = ""
        self._current_size = ""

        # Download links
        self._in_dlb_link = False
        self._current_dl_href = ""
        self._in_dlb_span = False
        self._dlb_span_text = ""
        self._current_download_links: list[dict[str, str]] = []

    def _reset_entry(self) -> None:
        self._current_release_name = ""
        self._current_size = ""
        self._current_download_links = []

    def _emit_entry(self) -> None:
        if not self._current_release_name or not self._current_download_links:
            return
        self.releases.append(
            {
                "release_name": self._current_release_name,
                "size": self._current_size,
                "download_links": self._current_download_links.copy(),
            }
        )

    def handle_starttag(  # noqa: C901
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()

        # Entry boundary
        if tag == "div":
            if self._in_entry:
                self._entry_div_depth += 1
            elif "entry" in classes:
                self._in_entry = True
                self._entry_div_depth = 0
                self._reset_entry()

        if not self._in_entry:
            return

        # Release name: <span class="morespec">
        if tag == "span" and "morespec" in classes:
            self._in_morespec = True
            self._current_release_name = ""

        # Audiotag: <span class="audiotag">
        if tag == "span" and "audiotag" in classes:
            self._in_audiotag = True
            self._small_text = ""
            self._audiotag_text = ""

        # Small label inside audiotag
        if tag == "small" and self._in_audiotag:
            self._in_small = True

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
        if self._in_morespec:
            self._current_release_name += data

        if self._in_small and self._in_audiotag:
            self._small_text += data

        if self._in_audiotag and not self._in_small:
            self._audiotag_text += data

        if self._in_dlb_span:
            self._dlb_span_text += data

    def handle_endtag(self, tag: str) -> None:  # noqa: C901
        if tag == "span":
            if self._in_morespec:
                self._in_morespec = False
                self._current_release_name = self._current_release_name.strip()

            if self._in_dlb_span:
                self._in_dlb_span = False

            if self._in_audiotag and not self._in_small and not self._in_dlb_span:
                # End of audiotag span
                label = self._small_text.strip().rstrip(":")
                value = self._audiotag_text.strip()
                if label.lower() == "größe" and value:
                    self._current_size = value
                self._in_audiotag = False

        if tag == "small" and self._in_small:
            self._in_small = False

        if tag == "a" and self._in_dlb_link:
            self._in_dlb_link = False
            hoster = self._dlb_span_text.strip()
            if hoster and self._current_dl_href:
                self._current_download_links.append(
                    {"hoster": hoster, "link": self._current_dl_href}
                )
            self._current_dl_href = ""

        if tag == "div" and self._in_entry:
            if self._entry_div_depth > 0:
                self._entry_div_depth -= 1
            else:
                self._in_entry = False
                self._emit_entry()


class FilmfansPlugin:
    """Python plugin for filmfans.org using httpx."""

    name = "filmfans"
    version = "1.0.0"
    mode = "httpx"
    provides = "download"
    default_language = "de"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
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

    async def _search_api(self, query: str) -> list[dict[str, str | int]]:
        """Execute JSON search API and return movie entries."""
        client = await self._ensure_client()

        try:
            resp = await client.get(
                f"{self.base_url}/api/v2/search",
                params={"q": query, "ql": "DE"},
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("filmfans_search_failed", query=query, error=str(exc))
            return []

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            log.warning("filmfans_invalid_json", query=query)
            return []

        movies = data.get("result", [])
        if not isinstance(movies, list):
            return []

        log.info("filmfans_search_api", query=query, count=len(movies))
        return movies

    async def _fetch_movie_page(
        self, url_id: str
    ) -> list[dict[str, str | list[dict[str, str]]]]:
        """Fetch a movie page and parse its releases."""
        client = await self._ensure_client()

        url = f"{self.base_url}/{url_id}"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "filmfans_movie_page_failed",
                url_id=url_id,
                error=str(exc),
            )
            return []

        parser = _ReleaseParser(self.base_url)
        parser.feed(resp.text)

        log.info(
            "filmfans_movie_page",
            url_id=url_id,
            releases=len(parser.releases),
        )
        return parser.releases

    def _build_search_result(
        self,
        movie: dict[str, str | int],
        release: dict[str, str | list[dict[str, str]]],
    ) -> SearchResult:
        """Convert a movie + release entry to a SearchResult."""
        title = str(movie.get("title", ""))
        year = movie.get("year")
        url_id = str(movie.get("url_id", ""))

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
            published_date=str(year) if year else None,
            category=2000,
        )

    async def search(
        self,
        query: str,
        category: int | None = None,
    ) -> list[SearchResult]:
        """Search filmfans.org and return results.

        Uses JSON search API to find movies, then fetches each movie page
        to extract individual releases with download links.
        """
        # Movies only — reject non-movie categories
        if category is not None and not (2000 <= category < 3000):
            return []

        if not query:
            return []

        await self._ensure_client()

        movies = await self._search_api(query)
        if not movies:
            return []

        # Fetch movie pages with bounded concurrency
        sem = asyncio.Semaphore(_MAX_CONCURRENT_DETAIL)
        results: list[SearchResult] = []

        async def _process_movie(movie: dict[str, str | int]) -> list[SearchResult]:
            url_id = str(movie.get("url_id", ""))
            if not url_id:
                return []

            async with sem:
                releases = await self._fetch_movie_page(url_id)

            movie_results = []
            for release in releases:
                sr = self._build_search_result(movie, release)
                if sr.download_link:
                    movie_results.append(sr)
            return movie_results

        tasks = [_process_movie(m) for m in movies]
        task_results = await asyncio.gather(*tasks)

        for movie_results in task_results:
            results.extend(movie_results)
            if len(results) >= _MAX_RESULTS:
                break

        return results[:_MAX_RESULTS]

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Used to add timestamp to external links for cache busting
def _timestamp() -> int:
    """Return current Unix timestamp in milliseconds."""
    return int(time.time() * 1000)


plugin = FilmfansPlugin()
