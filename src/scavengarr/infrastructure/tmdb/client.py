"""TMDB API client — async httpx implementation with caching."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from scavengarr.domain.entities.stremio import StremioMetaPreview
from scavengarr.domain.ports.cache import CachePort

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.themoviedb.org/3"
_POSTER_BASE = "https://image.tmdb.org/t/p/w500"

# Cache TTLs (seconds)
_TTL_FIND = 86_400  # 24 hours
_TTL_TRENDING = 21_600  # 6 hours
_TTL_SEARCH = 3_600  # 1 hour


class HttpxTmdbClient:
    """Async TMDB client using httpx + CachePort.

    Implements ``TmdbClientPort`` from domain.ports.tmdb.
    """

    def __init__(
        self,
        *,
        api_key: str,
        http_client: httpx.AsyncClient,
        cache: CachePort,
    ) -> None:
        self._api_key = api_key
        self._http = http_client
        self._cache = cache

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _params(self, **extra: Any) -> dict[str, Any]:
        """Build query params with api_key and German locale."""
        return {"api_key": self._api_key, "language": "de-DE", **extra}

    async def _get(self, path: str, **extra: Any) -> dict[str, Any] | None:
        """GET request with error handling. Returns parsed JSON or None."""
        url = f"{_BASE_URL}{path}"
        try:
            resp = await self._http.get(url, params=self._params(**extra))
            if resp.status_code == 401:
                logger.error("TMDB API key invalid (401)")
                return None
            if resp.status_code == 404:
                logger.debug("TMDB resource not found: %s", path)
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            logger.warning("TMDB HTTP error for %s", path, exc_info=True)
            return None
        except httpx.HTTPError:
            logger.warning("TMDB network error for %s", path, exc_info=True)
            return None

    @staticmethod
    def _poster_url(poster_path: str | None) -> str:
        if not poster_path:
            return ""
        return f"{_POSTER_BASE}{poster_path}"

    @staticmethod
    def _extract_imdb_id(item: dict[str, Any]) -> str:
        """Extract IMDb ID from a TMDB item, falling back to tmdb-prefixed ID."""
        imdb_id = item.get("imdb_id") or item.get("external_ids", {}).get("imdb_id")
        if imdb_id:
            return imdb_id
        # Fallback: construct an ID from TMDB's own ID
        tmdb_id = item.get("id", "")
        return f"tmdb:{tmdb_id}" if tmdb_id else ""

    def _movie_to_preview(self, movie: dict[str, Any]) -> StremioMetaPreview:
        release_date = movie.get("release_date", "")
        return StremioMetaPreview(
            id=f"tt{movie['id']}" if not str(movie.get("id", "")).startswith("tt") else str(movie["id"]),
            type="movie",
            name=movie.get("title", movie.get("original_title", "")),
            poster=self._poster_url(movie.get("poster_path")),
            description=movie.get("overview", ""),
            release_info=release_date[:4] if release_date else "",
            imdb_rating=str(movie.get("vote_average", "")) if movie.get("vote_average") else "",
        )

    def _tv_to_preview(self, show: dict[str, Any]) -> StremioMetaPreview:
        first_air = show.get("first_air_date", "")
        return StremioMetaPreview(
            id=f"tt{show['id']}" if not str(show.get("id", "")).startswith("tt") else str(show["id"]),
            type="series",
            name=show.get("name", show.get("original_name", "")),
            poster=self._poster_url(show.get("poster_path")),
            description=show.get("overview", ""),
            release_info=first_air[:4] if first_air else "",
            imdb_rating=str(show.get("vote_average", "")) if show.get("vote_average") else "",
        )

    # ------------------------------------------------------------------
    # Public API (TmdbClientPort)
    # ------------------------------------------------------------------

    async def find_by_imdb_id(self, imdb_id: str) -> dict[str, Any] | None:
        """Lookup TMDB entry by IMDb ID. Returns movie/TV metadata or None."""
        cache_key = f"tmdb:find:{imdb_id}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(
            f"/find/{imdb_id}",
            external_source="imdb_id",
        )
        if data is None:
            return None

        # /find returns lists grouped by media type
        for media_type in ("movie_results", "tv_results"):
            results = data.get(media_type, [])
            if results:
                result = results[0]
                await self._cache.set(cache_key, result, ttl=_TTL_FIND)
                return result

        return None

    async def get_german_title(self, imdb_id: str) -> str | None:
        """Get the German title for an IMDb ID. None if not found."""
        result = await self.find_by_imdb_id(imdb_id)
        if result is None:
            return None
        # Movies use "title", TV shows use "name"
        return result.get("title") or result.get("name") or None

    async def trending_movies(self, page: int = 1) -> list[StremioMetaPreview]:
        """Fetch trending movies (German locale)."""
        cache_key = f"tmdb:trending:movie:{page}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get("/trending/movie/week", page=page)
        if data is None:
            return []

        previews = [self._movie_to_preview(m) for m in data.get("results", [])]
        await self._cache.set(cache_key, previews, ttl=_TTL_TRENDING)
        return previews

    async def trending_tv(self, page: int = 1) -> list[StremioMetaPreview]:
        """Fetch trending TV shows (German locale)."""
        cache_key = f"tmdb:trending:tv:{page}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get("/trending/tv/week", page=page)
        if data is None:
            return []

        previews = [self._tv_to_preview(s) for s in data.get("results", [])]
        await self._cache.set(cache_key, previews, ttl=_TTL_TRENDING)
        return previews

    async def search_movies(
        self, query: str, page: int = 1
    ) -> list[StremioMetaPreview]:
        """Search movies by query (German locale)."""
        cache_key = f"tmdb:search:movie:{query}:{page}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get("/search/movie", query=query, page=page)
        if data is None:
            return []

        previews = [self._movie_to_preview(m) for m in data.get("results", [])]
        await self._cache.set(cache_key, previews, ttl=_TTL_SEARCH)
        return previews

    async def search_tv(
        self, query: str, page: int = 1
    ) -> list[StremioMetaPreview]:
        """Search TV shows by query (German locale)."""
        cache_key = f"tmdb:search:tv:{query}:{page}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get("/search/tv", query=query, page=page)
        if data is None:
            return []

        previews = [self._tv_to_preview(s) for s in data.get("results", [])]
        await self._cache.set(cache_key, previews, ttl=_TTL_SEARCH)
        return previews
