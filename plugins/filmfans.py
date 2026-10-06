"""filmfans.org Python plugin for Scavengarr.

Scrapes filmfans.org (German movie DDL site) with:
- httpx requests via ``_fetch_text()``; the site sits behind a Cloudflare
  Turnstile challenge, so pages and APIs load through the browser fallback
  (``playwright.browser_fallback``)
- JSON search API: GET /api/v2/search?q={query}&ql=DE
- Server-rendered movie pages at /{url_id} with all releases
- Download links via /external/{hash} redirect URLs
- Movies only (category 2000)

No authentication required.
"""

from __future__ import annotations

import asyncio
import re
import time

from selectolax.lexbor import LexborHTMLParser, LexborNode

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.dom import outermost, parse_page
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["filmfans.org"]

# Regex to extract the initMovie hash from movie page <script> content
_INIT_MOVIE_RE = re.compile(r"initMovie\(\s*'([^']+)'")


class _ReleaseParser:
    """Parse release entries from a filmfans.org movie page (selectolax).

    Each release is a ``<div class="entry">`` containing:
    - ``<span class="morespec">Release.Name.Here</span>`` (release/scene name)
    - ``<span class="audiotag"><small>Größe:</small> 37.3 GB</span>`` (size)
    - ``<a class="dlb row" href="/external/{hash}?_={ts}">``
      ``<div class="col"><span>hoster_name</span></div></a>`` (download links)
    """

    def __init__(self, base_url: str) -> None:
        self.releases: list[dict[str, str | list[dict[str, str]]]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        tree = LexborHTMLParser(html)
        # An entry inside another one is part of the outer entry
        for entry in outermost(tree.css("div.entry")):
            self._add_entry(entry)

    def _add_entry(self, entry: LexborNode) -> None:
        """Add the release of *entry* when it has a name and download links."""
        # The last release name counts, and the last size
        names = entry.css("span.morespec")
        release_name = names[-1].text().strip() if names else ""
        size = ""
        for tag in entry.css("span.audiotag"):
            size = _audiotag_size(tag) or size
        download_links: list[dict[str, str]] = []
        for link in entry.css("a.dlb"):
            href = link.attributes.get("href") or ""
            # Hoster name: the (last) <span> inside the link
            spans = link.css("span")
            hoster = spans[-1].text().strip() if spans else ""
            if href and hoster:
                if href.startswith("/"):
                    href = f"{self._base_url}{href}"
                download_links.append({"hoster": hoster, "link": href})
        if release_name and download_links:
            self.releases.append(
                {
                    "release_name": release_name,
                    "size": size,
                    "download_links": download_links,
                }
            )


def _audiotag_size(tag: LexborNode) -> str:
    """The size a "Größe:" audiotag names, else an empty string.

    The label is the tag's ``<small>`` text, the value the rest of its text.
    """
    label = value = ""
    for child in tag.iter(include_text=True):
        if child.tag == "small":
            label += child.text()
        else:
            value += child.text()
    if label.strip().rstrip(":").lower() == "größe":
        return value.strip()
    return ""


class FilmfansPlugin(HttpxPluginBase):
    """Python plugin for filmfans.org using httpx."""

    name = "filmfans"
    version = "1.0.0"
    mode = "httpx"
    provides = "download"

    _domains = _DOMAINS

    async def _search_api(self, query: str) -> list[dict[str, str | int]]:
        """Execute JSON search API and return movie entries."""
        body = await self._fetch_text(
            f"{self.base_url}/api/v2/search",
            params={"q": query, "ql": "DE"},
            context="search",
        )
        data = self._parse_json_text(body, "search")
        if data is None:
            return []

        movies = data.get("result", [])
        if not isinstance(movies, list):
            return []

        self._log.info("filmfans_search_api", query=query, count=len(movies))
        return movies

    async def _fetch_movie_page(
        self, url_id: str
    ) -> list[dict[str, str | list[dict[str, str]]]]:
        """Fetch a movie page and parse its releases.

        Releases are loaded via JavaScript (``initMovie()``), not in the static
        HTML.  We extract the hash from the page, then call the
        ``/api/v1/{hash}`` endpoint which returns JSON with an ``html`` field
        containing the release entries that ``_ReleaseParser`` expects.
        """
        page = await self._fetch_text(f"{self.base_url}/{url_id}", context=url_id)
        if page is None:
            return []

        # Extract initMovie hash from the page script
        m = _INIT_MOVIE_RE.search(page)
        if not m:
            self._log.warning("filmfans_no_init_hash", url_id=url_id)
            return []

        # Fetch releases via API
        body = await self._fetch_text(
            f"{self.base_url}/api/v1/{m.group(1)}",
            params={"_": str(_timestamp())},
            context=url_id,
        )
        data = self._parse_json_text(body, url_id)
        if data is None:
            return []

        html = data.get("html", "")
        if not html:
            return []

        parser = await parse_page(_ReleaseParser(self.base_url), html)

        self._log.info(
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
        season: int | None = None,
        episode: int | None = None,
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
        sem = self._new_semaphore()
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
            if len(results) >= self.effective_max_results:
                break

        # /external/<hash> links sit behind Cloudflare: resolve them to the
        # hoster / link container (only for the results returned)
        return await self._resolve_result_links(results[: self.effective_max_results])


# Used to add timestamp to external links for cache busting
def _timestamp() -> int:
    """Return current Unix timestamp in milliseconds."""
    return int(time.time() * 1000)


plugin = FilmfansPlugin()
