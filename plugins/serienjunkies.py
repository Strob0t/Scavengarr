"""serienjunkies.org Python plugin for Scavengarr.

Scrapes serienjunkies.org (German TV series download site) with:
- httpx for all requests (SSR HTML search + JSON releases API)
- GET /serie/search?q={query} for keyword search (returns all matches, no pagination)
- GET /serie/{slug} detail page to extract media ID from data-mediaid attribute
- GET /api/media/{mediaId}/releases for structured release data (JSON)
- Season/episode filtering from structured release metadata
- Category 5000 (TV) for all results (TV-only site)
- Bounded concurrency for detail page + API scraping

Single domain: serienjunkies.org (no active alternatives).
No authentication required for search/releases.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["serienjunkies.org"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SIZE_UNITS: dict[str, int] = {
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}

_HOSTER_NAMES: dict[str, str] = {
    "filer": "Filer.net",
    "ddownload": "DDownload",
    "rapidgator": "RapidGator",
    "turbobit": "Turbobit",
    "nitroflare": "Nitroflare",
    "uploaded": "Uploaded",
}


# ---------------------------------------------------------------------------
# Search result parser (for /serie/search?q= page)
# ---------------------------------------------------------------------------
class _SearchResultParser(HTMLParser):
    """Parse serienjunkies.org search results page.

    Results are in a ``<table>`` with rows like::

        <tr><td><a href="/serie/breaking-bad">Breaking Bad</a></td></tr>
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_td = False
        self._in_a = False
        self._current_href = ""
        self._current_title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "td":
            self._in_td = True

        if tag == "a" and self._in_td:
            href = attr_dict.get("href", "") or ""
            if href.startswith("/serie/") and href != "/serie/search":
                self._in_a = True
                self._current_href = href
                self._current_title = ""

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._current_title += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            self._in_a = False
            title = self._current_title.strip()
            if title and self._current_href:
                slug = self._current_href.rstrip("/").rsplit("/", 1)[-1]
                if not any(r["slug"] == slug for r in self.results):
                    self.results.append(
                        {
                            "title": title,
                            "slug": slug,
                        }
                    )
            self._current_href = ""
            self._current_title = ""

        if tag == "td":
            self._in_td = False


# ---------------------------------------------------------------------------
# Detail page parser (extracts media ID from data-mediaid attribute)
# ---------------------------------------------------------------------------
class _DetailPageParser(HTMLParser):
    """Parse serienjunkies.org detail page for media ID.

    The page contains a Vue mount point::

        <div id="v-release-list" data-mediaid="..." data-mediatitle="...">
    """

    def __init__(self) -> None:
        super().__init__()
        self.media_id: str = ""
        self.media_title: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "div" and attr_dict.get("id") == "v-release-list":
            self.media_id = attr_dict.get("data-mediaid", "") or ""
            self.media_title = attr_dict.get("data-mediatitle", "") or ""


# ---------------------------------------------------------------------------
# Release helpers
# ---------------------------------------------------------------------------
def _size_to_bytes(value: int | float, unit: str) -> int:
    """Convert size value + unit to bytes."""
    multiplier = _SIZE_UNITS.get(unit.upper(), _SIZE_UNITS["MB"])
    return int(value * multiplier)


def _format_size(value: int | float, unit: str) -> str:
    """Format size as human-readable string."""
    if value <= 0:
        return ""
    if value == int(value):
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def _matches_season(release: dict, season: int | None) -> bool:
    """Check if a release matches the requested season."""
    if season is None:
        return True
    rel_season = release.get("season")
    return rel_season == season


def _matches_episode(release: dict, episode: int | None) -> bool:
    """Check if a release matches the requested episode."""
    if episode is None:
        return True
    rel_episode = release.get("episode")
    # Season packs (episode=None) match any episode request
    if rel_episode is None:
        return True
    return rel_episode == episode


def _build_description(release: dict) -> str:
    """Build description from release metadata."""
    parts: list[str] = []

    if release.get("resolution"):
        parts.append(release["resolution"])
    if release.get("source"):
        parts.append(release["source"])
    if release.get("encoding"):
        parts.append(release["encoding"])
    if release.get("audio"):
        parts.append(release["audio"])
    if release.get("language"):
        parts.append(release["language"])
    if release.get("group"):
        parts.append(f"[{release['group']}]")

    return " | ".join(parts) if parts else ""


def _hoster_display(hoster_id: str) -> str:
    """Get display name for a hoster ID."""
    return _HOSTER_NAMES.get(hoster_id, hoster_id)


_SEASON_EP_RE = re.compile(r"S(\d+)(?:E(\d+))?", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------
class SerienjunkiesPlugin(HttpxPluginBase):
    """Python plugin for serienjunkies.org using httpx."""

    name = "serienjunkies"
    provides = "download"
    default_language = "de"
    _domains = _DOMAINS
    _max_results = 1000

    categories: dict[int, str] = {  # noqa: RUF012
        5000: "TV",
        5010: "TV/WEB-DL",
        5020: "TV/Foreign",
        5030: "TV/SD",
        5040: "TV/HD",
        5045: "TV/UHD",
        5070: "TV/Anime",
        5080: "TV/Documentary",
    }

    async def _search_series(self, query: str) -> list[dict[str, str]]:
        """Search for series matching query.

        Returns list of dicts with 'title' and 'slug' keys.
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/serie/search",
            context="search",
            params={"q": query},
        )
        if resp is None:
            return []

        parser = _SearchResultParser()
        parser.feed(resp.text)

        self._log.info(
            "serienjunkies_search",
            query=query,
            results=len(parser.results),
        )
        return parser.results

    async def _get_media_id(self, slug: str) -> tuple[str, str]:
        """Fetch detail page and extract media ID.

        Returns (media_id, media_title).
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/serie/{slug}",
            context="detail",
        )
        if resp is None:
            return "", ""

        parser = _DetailPageParser()
        parser.feed(resp.text)
        return parser.media_id, parser.media_title

    async def _get_releases(self, media_id: str) -> dict:
        """Fetch releases from JSON API.

        Returns raw releases dict grouped by season key (e.g. 'S1', 'SP').
        """
        resp = await self._safe_fetch(
            f"{self.base_url}/api/media/{media_id}/releases",
            context="releases_api",
        )
        if resp is None:
            return {}

        data = self._safe_parse_json(resp, context="releases_api")
        if not isinstance(data, dict):
            return {}
        return data

    def _release_to_result(
        self,
        release: dict,
        title: str,
        slug: str,
    ) -> SearchResult | None:
        """Convert a single release dict to a SearchResult."""
        rel_name = release.get("name", "")
        if not rel_name:
            return None

        hosters = release.get("hoster", [])
        if not hosters:
            return None

        # Size
        size_val = release.get("sizevalue", 0)
        size_unit = release.get("sizeunit", "MB")
        size_bytes = _size_to_bytes(size_val, size_unit) if size_val else None
        size_str = _format_size(size_val, size_unit) if size_val else None

        source_url = f"{self.base_url}/serie/{slug}"
        release_id = release.get("_id", "")

        # Build download links for each hoster
        download_links = [
            {
                "hoster": _hoster_display(h),
                "link": f"{source_url}#release-{release_id}",
            }
            for h in hosters
        ]

        # Metadata
        metadata: dict[str, str] = {
            "release_group": release.get("group", ""),
            "resolution": release.get("resolution", ""),
            "source": release.get("source", ""),
            "encoding": release.get("encoding", ""),
            "audio": release.get("audio", ""),
            "language": release.get("language", ""),
            "hosters": ", ".join(_hoster_display(h) for h in hosters),
        }

        rel_season = release.get("season")
        rel_episode = release.get("episode")
        if rel_season is not None:
            metadata["season"] = str(rel_season)
        if rel_episode is not None:
            metadata["episode"] = str(rel_episode)

        return SearchResult(
            title=f"{title} - {rel_name}",
            release_name=rel_name,
            download_link=source_url,
            download_links=download_links,
            source_url=source_url,
            category=5000,
            size=str(size_bytes) if size_bytes else size_str,
            description=_build_description(release),
            metadata=metadata,
        )

    async def _scrape_series(
        self,
        series: dict[str, str],
        season: int | None,
        episode: int | None,
    ) -> list[SearchResult]:
        """Scrape a single series: detail page -> releases API -> SearchResults."""
        slug = series["slug"]
        series_title = series["title"]

        media_id, media_title = await self._get_media_id(slug)
        if not media_id:
            self._log.debug("serienjunkies_no_media_id", slug=slug)
            return []

        title = media_title or series_title

        releases_data = await self._get_releases(media_id)
        if not releases_data:
            return []

        results: list[SearchResult] = []

        for _season_key, season_data in releases_data.items():
            for release in season_data.get("items", []):
                if not _matches_season(release, season):
                    continue
                if not _matches_episode(release, episode):
                    continue

                result = self._release_to_result(release, title, slug)
                if result is not None:
                    results.append(result)

        self._log.info(
            "serienjunkies_series_scraped",
            slug=slug,
            releases=len(results),
        )
        return results

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search serienjunkies.org and return results with release info."""
        await self._ensure_client()
        await self._verify_domain()

        if not query:
            return []

        series_list = await self._search_series(query)
        if not series_list:
            return []

        # Limit series to scrape (each series can have many releases)
        max_series = 20
        series_list = series_list[:max_series]

        # Scrape detail pages + releases API with bounded concurrency
        sem = self._new_semaphore()

        async def _bounded(s: dict[str, str]) -> list[SearchResult]:
            async with sem:
                return await self._scrape_series(s, season, episode)

        gathered = await asyncio.gather(
            *[_bounded(s) for s in series_list],
            return_exceptions=True,
        )

        all_results: list[SearchResult] = []
        for result in gathered:
            if isinstance(result, list):
                all_results.extend(result)

        return all_results[: self.effective_max_results]


plugin = SerienjunkiesPlugin()
