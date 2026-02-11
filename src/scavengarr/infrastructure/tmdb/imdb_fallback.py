"""IMDB Suggest API fallback — title resolution without an API key."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import StremioMetaPreview
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

_SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/t/{imdb_id}.json"
_TTL_TITLE = 86_400  # 24 hours


class ImdbFallbackClient:
    """Title resolver using the free IMDB Suggest API.

    Implements ``TmdbClientPort`` so it can be used as a drop-in
    replacement when no TMDB API key is configured.  Only title
    resolution works — catalog and trending methods return empty lists.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        cache: CachePort,
    ) -> None:
        self._http = http_client
        self._cache = cache

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _fetch_suggest(self, imdb_id: str) -> dict[str, Any] | None:
        """Fetch and cache the IMDB Suggest API response for *imdb_id*."""
        cache_key = f"imdb:suggest:{imdb_id}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = _SUGGEST_URL.format(imdb_id=imdb_id)
        try:
            resp = await self._http.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning("imdb_suggest_failed", imdb_id=imdb_id, exc_info=True)
            return None

        entries = data.get("d", [])
        if not entries:
            return None

        # Find the entry matching the requested ID (first match)
        for entry in entries:
            if entry.get("id") == imdb_id:
                await self._cache.set(cache_key, entry, ttl=_TTL_TITLE)
                return entry

        # Fallback: use the first entry
        entry = entries[0]
        await self._cache.set(cache_key, entry, ttl=_TTL_TITLE)
        return entry

    # ------------------------------------------------------------------
    # TmdbClientPort implementation
    # ------------------------------------------------------------------

    async def find_by_imdb_id(self, imdb_id: str) -> dict[str, Any] | None:
        """Lookup title metadata via IMDB Suggest API."""
        entry = await self._fetch_suggest(imdb_id)
        if entry is None:
            return None

        title = entry.get("l", "")
        # Map to the same keys HttpxTmdbClient returns
        return {"title": title, "name": title, "id": entry.get("id", imdb_id)}

    async def get_german_title(self, imdb_id: str) -> str | None:
        """Return the title for an IMDb ID (English — IMDB has no locale)."""
        entry = await self._fetch_suggest(imdb_id)
        if entry is None:
            return None
        title = entry.get("l")
        if title:
            log.info("imdb_title_resolved", imdb_id=imdb_id, title=title)
        return title or None

    async def get_title_by_tmdb_id(self, tmdb_id: int, media_type: str) -> str | None:
        """Cannot resolve TMDB IDs without TMDB API."""
        return None

    async def trending_movies(self, page: int = 1) -> list[StremioMetaPreview]:
        """Not available without TMDB API."""
        return []

    async def trending_tv(self, page: int = 1) -> list[StremioMetaPreview]:
        """Not available without TMDB API."""
        return []

    async def search_movies(
        self, query: str, page: int = 1
    ) -> list[StremioMetaPreview]:
        """Not available without TMDB API."""
        return []

    async def search_tv(self, query: str, page: int = 1) -> list[StremioMetaPreview]:
        """Not available without TMDB API."""
        return []
