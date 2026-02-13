"""nox.to Python plugin for Scavengarr.

Scrapes nox.to (German DDL archive) via its JSON API:
- GET /api/frontend/search/{query} for search (returns releases + media)
- GET /api/frontend/releases/latest/{days} for browse (empty query)

Covers movies and TV episodes. Games are excluded (no Torznab mapping).
Download links point to the release page (actual downloads are behind reCAPTCHA).
Two domains: nox.to (primary), nox.tv (alias, 301 redirect).
No authentication required.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["nox.to", "nox.tv"]

# Escalating time windows for browse mode (empty query).
# Start small, expand until we have enough results.
_BROWSE_DAYS = [1, 3, 7, 14, 30]

# Minimum results before stopping browse escalation
_BROWSE_MIN_RESULTS = 50

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TYPE_TO_CATEGORY: dict[str, int] = {
    "movie": 2000,
    "episode": 5000,
}

# Reverse: Torznab category range → nox type
_CATEGORY_TO_TYPE: dict[int, str] = {
    2000: "movie",
    5000: "episode",
}

_COVER_URL_TEMPLATE = "/api/frontend/file/cover/thumbnail/{cover_id}.jpg"


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


def _category_for_type(release_type: str) -> int | None:
    """Map nox release type to Torznab category. Returns None for unsupported."""
    return _TYPE_TO_CATEGORY.get(release_type)


def _extract_year(title: str) -> str | None:
    """Extract a 4-digit year from title patterns like 'Iron Man [2008]' or similar."""
    m = re.search(r"\b((?:19|20)\d{2})\b", title)
    return m.group(1) if m else None


class NoxPlugin(HttpxPluginBase):
    """Python plugin for nox.to using httpx (JSON API)."""

    name = "nox"
    provides = "download"
    default_language = "de"
    _domains = _DOMAINS

    # ------------------------------------------------------------------
    # Search API
    # ------------------------------------------------------------------

    async def _search_api(self, query: str) -> dict:
        """Execute search API and return the full result dict.

        Returns dict with keys: releases, media, actor, director, etc.
        """
        encoded = quote(query, safe="")
        resp = await self._safe_fetch(
            f"{self.base_url}/api/frontend/search/{encoded}",
            context="search",
        )
        if resp is None:
            return {}

        data = self._safe_parse_json(resp, context="search")
        if not isinstance(data, dict):
            return {}

        return data.get("result", {}) if isinstance(data.get("result"), dict) else {}

    # ------------------------------------------------------------------
    # Browse API (latest releases)
    # ------------------------------------------------------------------

    async def _browse_latest(self) -> list[dict]:
        """Fetch latest releases, escalating time window until enough results."""
        for days in _BROWSE_DAYS:
            resp = await self._safe_fetch(
                f"{self.base_url}/api/frontend/releases/latest/{days}",
                context="browse",
            )
            if resp is None:
                continue

            data = self._safe_parse_json(resp, context="browse")
            if not isinstance(data, dict):
                continue

            releases = data.get("result", [])
            if not isinstance(releases, list):
                continue

            self._log.info("nox_browse", days=days, count=len(releases))

            if len(releases) >= _BROWSE_MIN_RESULTS:
                return releases[: self.effective_max_results]

        # Return whatever we got from the last attempt
        return releases[: self.effective_max_results] if releases else []

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------

    def _extract_media_metadata(self, media: dict | None) -> dict[str, str]:
        """Extract metadata fields from the media dict."""
        if not media:
            return {
                "imdb_id": "",
                "rating": "",
                "genres": "",
                "description": "",
                "poster": "",
                "runtime": "",
            }

        imdb_id = media.get("imdbid", "") or ""
        imdb_rating = (
            str(media.get("imdbrating", "")) if media.get("imdbrating") else ""
        )

        genre_list = media.get("genres", [])
        genres = ""
        if isinstance(genre_list, list):
            genres = ", ".join(str(g) for g in genre_list if g)

        desc = media.get("description", "") or ""
        if len(desc) > 300:
            desc = desc[:297] + "..."

        runtime = str(media.get("duration", "")) if media.get("duration") else ""

        cover_id = media.get("cover", "") or ""
        poster = ""
        if cover_id:
            poster = f"{self.base_url}{_COVER_URL_TEMPLATE.format(cover_id=cover_id)}"

        return {
            "imdb_id": imdb_id,
            "rating": imdb_rating,
            "genres": genres,
            "description": desc,
            "poster": poster,
            "runtime": runtime,
        }

    def _build_result(
        self,
        release: dict,
        media: dict | None = None,
    ) -> SearchResult | None:
        """Convert a release + optional media dict into a SearchResult."""
        slug = release.get("slug", "")
        release_type = release.get("type", "")

        # Skip unsupported types (games, etc.)
        category = _category_for_type(release_type)
        if category is None:
            return None

        # Title: prefer media title (cleaner), fallback to release title
        media_title = media.get("title", "") if media else ""
        release_title = release.get("title", "")
        title = media_title or release_title

        if not title:
            return None

        # Year from media or release name
        year = None
        if media and media.get("productionyear"):
            year = str(media["productionyear"])
        if not year:
            scene_name = release.get("name", "")
            year = _extract_year(scene_name) or _extract_year(release_title)

        display_title = f"{title} ({year})" if year else title

        # Download link = release page URL
        download_link = f"{self.base_url}/release/{slug}" if slug else ""
        if not download_link:
            return None

        # Extract metadata from media + release
        meta = self._extract_media_metadata(media)

        codec = release.get("codec", "") or ""
        video = release.get("video", "") or ""
        audio = release.get("audio", "") or ""

        published = release.get("publishat") or release.get("createdAt") or ""

        return SearchResult(
            title=display_title,
            download_link=download_link,
            source_url=download_link,
            release_name=release.get("name") or None,
            size=_format_size(release.get("size"), release.get("sizeunit")),
            published_date=published[:10] if published else None,
            category=category,
            description=meta["description"] or None,
            metadata={
                "imdb_id": meta["imdb_id"],
                "rating": meta["rating"],
                "genres": meta["genres"],
                "runtime": meta["runtime"],
                "poster": meta["poster"],
                "codec": codec,
                "video": video,
                "audio": audio,
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

        Uses the site's JSON API for search or browse (latest releases).
        Supports movie (2000) and TV (5000) categories.
        """
        # Determine allowed type from category filter
        allowed_type: str | None = None
        if category is not None:
            if 2000 <= category < 3000:
                allowed_type = "movie"
            elif 5000 <= category < 6000:
                allowed_type = "episode"
            else:
                # Unsupported category (games, music, etc.)
                return []

        await self._ensure_client()
        await self._verify_domain()

        if query:
            return await self._search_with_query(query, allowed_type)
        return await self._browse_with_latest(allowed_type)

    async def _search_with_query(
        self, query: str, allowed_type: str | None
    ) -> list[SearchResult]:
        """Search by query: use search API and cross-reference media."""
        result = await self._search_api(query)
        if not result:
            return []

        releases = result.get("releases", [])
        media_list = result.get("media", [])

        if not isinstance(releases, list):
            return []

        # Build title→media lookup for cross-referencing
        media_lookup: dict[str, dict] = {}
        if isinstance(media_list, list):
            for m in media_list:
                m_title = m.get("title", "")
                if m_title:
                    media_lookup[m_title] = m

        self._log.info(
            "nox_search",
            query=query,
            releases=len(releases),
            media=len(media_lookup),
        )

        results: list[SearchResult] = []
        for release in releases:
            # Filter by type if category was specified
            release_type = release.get("type", "")
            if allowed_type and release_type != allowed_type:
                continue

            # Cross-reference: match release title to media entry
            release_title = release.get("title", "")
            media = media_lookup.get(release_title)

            sr = self._build_result(release, media)
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results

    async def _browse_with_latest(self, allowed_type: str | None) -> list[SearchResult]:
        """Browse latest releases (empty query)."""
        releases = await self._browse_latest()
        if not releases:
            return []

        results: list[SearchResult] = []
        for release in releases:
            # Filter by type if category was specified
            release_type = release.get("type", "")
            if allowed_type and release_type != allowed_type:
                continue

            # Browse results have _media embedded
            media = (
                release.get("_media")
                if isinstance(release.get("_media"), dict)
                else None
            )

            sr = self._build_result(release, media)
            if sr is not None:
                results.append(sr)
                if len(results) >= self.effective_max_results:
                    break

        return results


plugin = NoxPlugin()
