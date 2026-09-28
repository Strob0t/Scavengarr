"""nox.to Python plugin for Scavengarr.

Scrapes nox.to (German DDL archive) via its JSON API:
- GET /api/search?q={query}&page={n} for search (media entries, 20 per page)
- GET /api/media/{slug} for a media entry's releases and metadata
- GET /api/releases/recent/{days} for browse (empty query)

Covers movies, TV series and documentaries. Games, e-books and audio are
excluded (no Torznab mapping). Download links point to the release page
(``/media/{slug}?release={id}``); the actual downloads sit behind a captcha.
Two domains: nox.to (primary), nox.tv (alias, 301 redirect).
No authentication required.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["nox.to", "nox.tv"]

# Search pages of media entries (20 per page); each entry has 1..n releases
_MAX_SEARCH_PAGES = 10

# Escalating time windows for browse mode (empty query).
# Start small, expand until we have enough results.
_BROWSE_DAYS = [1, 3, 7]

# Minimum results before stopping browse escalation
_BROWSE_MIN_RESULTS = 50

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TYPE_TO_CATEGORY: dict[str, int] = {
    "movie": 2000,
    "series": 5000,
    "doku": 5080,
}

_POSTER_URL_TEMPLATE = "/api/image/w342/{path}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_size(size_str: str | None, unit_str: str | None) -> str | None:
    """Format size + unit into a human-readable string like '1481 MB'."""
    if not size_str:
        return None
    size_str = str(size_str).strip()
    if not size_str:
        return None
    unit = str(unit_str or "MB").strip()
    return f"{size_str} {unit}"


def _category_for_type(media_type: str) -> int | None:
    """Map nox media type to Torznab category. Returns None for unsupported."""
    return _TYPE_TO_CATEGORY.get(media_type)


def _extract_year(title: str) -> str | None:
    """Extract a 4-digit year from title patterns like 'Iron Man [2008]' or similar."""
    m = re.search(r"\b((?:19|20)\d{2})\b", title)
    return m.group(1) if m else None


def _genre_names(genres: object) -> str:
    """Join genre names (``[{"name": ...}]`` or plain strings)."""
    if not isinstance(genres, list):
        return ""
    names = [g.get("name", "") if isinstance(g, dict) else str(g) for g in genres]
    return ", ".join(n for n in names if n)


class NoxPlugin(HttpxPluginBase):
    """Python plugin for nox.to using httpx (JSON API)."""

    name = "nox"
    version = "1.1.0"
    provides = "download"
    default_language = "de"
    _domains = _DOMAINS

    # ------------------------------------------------------------------
    # API calls
    # ------------------------------------------------------------------

    async def _get_json(self, url: str, *, context: str, **kwargs: Any) -> Any:
        """GET *url* and return the parsed JSON body (``None`` on failure)."""
        resp = await self._safe_fetch(url, context=context, **kwargs)
        if resp is None:
            return None
        return self._safe_parse_json(resp, context=context)

    async def _search_media(self, query: str) -> list[dict[str, Any]]:
        """Collect media entries for *query* across search pages."""
        media: list[dict[str, Any]] = []
        page, total_pages = 1, 1
        while page <= min(total_pages, _MAX_SEARCH_PAGES):
            data = await self._get_json(
                f"{self.base_url}/api/search",
                context="search",
                params={"q": query, "page": str(page)},
            )
            if not isinstance(data, dict):
                break
            items = data.get("items")
            if not isinstance(items, list) or not items:
                break
            media.extend(i for i in items if isinstance(i, dict))
            pages = data.get("totalPages")
            total_pages = pages if isinstance(pages, int) else 1
            page += 1

        self._log.info("nox_search", query=query, media=len(media), pages=page - 1)
        return media

    async def _get_media(self, slug: str) -> dict[str, Any] | None:
        """Fetch a media entry with its releases."""
        data = await self._get_json(
            f"{self.base_url}/api/media/{slug}", context="media"
        )
        return data if isinstance(data, dict) else None

    async def _browse_latest(self) -> list[dict[str, Any]]:
        """Fetch recent releases, escalating the time window until enough."""
        releases: list[dict[str, Any]] = []
        for days in _BROWSE_DAYS:
            data = await self._get_json(
                f"{self.base_url}/api/releases/recent/{days}", context="browse"
            )
            if not isinstance(data, list):
                continue
            releases = [r for r in data if isinstance(r, dict)]
            self._log.info("nox_browse", days=days, count=len(releases))
            if len(releases) >= _BROWSE_MIN_RESULTS:
                break
        return releases

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------

    def _poster_url(self, path: object) -> str:
        if not isinstance(path, str) or not path:
            return ""
        return f"{self.base_url}{_POSTER_URL_TEMPLATE.format(path=path.lstrip('/'))}"

    def _build_result(
        self,
        release: dict[str, Any],
        media: dict[str, Any],
    ) -> SearchResult | None:
        """Convert a release + its media entry into a SearchResult.

        *media* is the full ``/api/media/{slug}`` entry in search mode and a
        stand-in built from the release's ``media*`` fields in browse mode.
        """
        category = _category_for_type(str(media.get("type", "")))
        media_slug = media.get("slug", "")
        release_id = release.get("id")
        title = media.get("title", "")
        if category is None or not media_slug or release_id is None or not title:
            return None

        scene_name = release.get("name", "") or ""
        year = media.get("productionYear") or _extract_year(scene_name)
        display_title = f"{title} ({year})" if year else title
        link = f"{self.base_url}/media/{media_slug}?release={release_id}"

        desc = media.get("description", "") or ""
        if len(desc) > 300:
            desc = desc[:297] + "..."
        rating = media.get("imdbRating")
        duration = media.get("duration")
        published = release.get("publishAt") or release.get("createdAt") or ""

        return SearchResult(
            title=display_title,
            download_link=link,
            source_url=link,
            release_name=scene_name or None,
            size=_format_size(release.get("size"), release.get("sizeUnit")),
            published_date=published[:10] if published else None,
            category=category,
            description=desc or None,
            metadata={
                "imdb_id": media.get("imdbId", "") or "",
                "rating": str(rating) if rating else "",
                "genres": _genre_names(media.get("genres")),
                "runtime": str(duration) if duration else "",
                "poster": self._poster_url(media.get("posterUrl")),
                "codec": release.get("codec", "") or "",
                "video": release.get("video", "") or "",
                "audio": release.get("audio", "") or "",
            },
        )

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
        """Search nox.to and return results.

        Uses the site's JSON API for search or browse (recent releases).
        Supports movie (2000), TV (5000) and documentary (5080) categories.
        """
        allowed = {
            t
            for t, cat in _TYPE_TO_CATEGORY.items()
            if self._category_matches(category, cat)
        }
        if not allowed:
            return []

        await self._ensure_client()
        await self._verify_domain()

        if query:
            return await self._search_with_query(query, allowed)
        return await self._browse_with_latest(allowed)

    async def _search_with_query(
        self, query: str, allowed: set[str]
    ) -> list[SearchResult]:
        """Search media entries, then list the releases of each."""
        media_list = [
            m for m in await self._search_media(query) if m.get("type") in allowed
        ]
        if not media_list:
            return []

        sem = self._new_semaphore()

        async def _bounded(slug: str) -> dict[str, Any] | None:
            async with sem:
                return await self._get_media(slug)

        details = await asyncio.gather(
            *[_bounded(str(m.get("slug", ""))) for m in media_list]
        )

        results: list[SearchResult] = []
        for media in details:
            if media is None:
                continue
            releases = media.get("releases")
            if not isinstance(releases, list):
                continue
            for release in releases:
                if not isinstance(release, dict) or release.get("onlineLinks") == 0:
                    continue
                sr = self._build_result(release, media)
                if sr is not None:
                    results.append(sr)
                if len(results) >= self.effective_max_results:
                    return results
        return results

    async def _browse_with_latest(self, allowed: set[str]) -> list[SearchResult]:
        """Browse recent releases (empty query)."""
        results: list[SearchResult] = []
        for release in await self._browse_latest():
            if release.get("type") not in allowed:
                continue
            media = {
                "type": release.get("type"),
                "slug": release.get("mediaSlug"),
                "title": release.get("mediaTitle"),
                "imdbRating": release.get("imdbRating"),
                "posterUrl": release.get("mediaPosterUrl"),
            }
            sr = self._build_result(release, media)
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break
        return results


plugin = NoxPlugin()
