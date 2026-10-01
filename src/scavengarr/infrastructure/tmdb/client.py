"""TMDB API client — async httpx implementation with caching."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import StremioMetaPreview, TitleMatchInfo
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

_BASE_URL = "https://api.themoviedb.org/3"
_POSTER_BASE = "https://image.tmdb.org/t/p/w500"

# Cache TTLs (seconds)
_TTL_FIND = 86_400  # 24 hours
_TTL_TRENDING = 21_600  # 6 hours
_TTL_SEARCH = 3_600  # 1 hour
_TTL_IMDB_ID = 30 * 86_400  # a title's IMDb id does not change


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

    def _params(self, *, lang: str = "de-DE", **extra: Any) -> dict[str, Any]:
        """Build query params with api_key and locale."""
        return {"api_key": self._api_key, "language": lang, **extra}

    async def _get(
        self, path: str, *, lang: str = "de-DE", **extra: Any
    ) -> dict[str, Any] | None:
        """GET request with error handling. Returns parsed JSON or None."""
        url = f"{_BASE_URL}{path}"
        try:
            resp = await self._http.get(url, params=self._params(lang=lang, **extra))
            if resp.status_code == 401:
                log.error("tmdb_api_key_invalid", status=401)
                return None
            if resp.status_code == 404:
                log.debug("tmdb_resource_not_found", path=path)
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            log.warning("tmdb_http_error", path=path, exc_info=True)
            return None
        except httpx.HTTPError:
            log.warning("tmdb_network_error", path=path, exc_info=True)
            return None

    @staticmethod
    def _poster_url(poster_path: str | None) -> str:
        if not poster_path:
            return ""
        return f"{_POSTER_BASE}{poster_path}"

    async def _imdb_id(self, endpoint: str, tmdb_id: int) -> str:
        """IMDb id of a TMDB title (``""`` when TMDB knows none)."""
        cache_key = f"tmdb:imdb:{endpoint}:{tmdb_id}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached
        data = await self._get(f"/{endpoint}/{tmdb_id}/external_ids")
        if data is None:
            return ""  # no answer: asked again next time
        imdb_id = data.get("imdb_id") or ""
        await self._cache.set(cache_key, imdb_id, ttl=_TTL_IMDB_ID)
        return imdb_id

    async def _previews(
        self,
        items: list[dict[str, Any]],
        endpoint: str,
        to_preview: Callable[[dict[str, Any], str], StremioMetaPreview],
    ) -> list[StremioMetaPreview]:
        """Catalog previews of the titles Stremio can open.

        Stremio opens a catalog item through a meta addon for its id prefix:
        Cinemeta knows IMDb ids, none of the default addons a ``tmdb:`` id
        ("No addons were requested for this meta!"). TMDB lists carry no
        IMDb ids, so each title's is looked up; titles without one are left
        out.
        """
        titles = [item for item in items if isinstance(item.get("id"), int)]
        imdb_ids = await asyncio.gather(
            *(self._imdb_id(endpoint, item["id"]) for item in titles)
        )
        return [
            to_preview(item, imdb_id)
            for item, imdb_id in zip(titles, imdb_ids, strict=True)
            if imdb_id
        ]

    def _movie_to_preview(
        self, movie: dict[str, Any], imdb_id: str
    ) -> StremioMetaPreview:
        release_date = movie.get("release_date", "")
        return StremioMetaPreview(
            id=imdb_id,
            type="movie",
            name=movie.get("title", movie.get("original_title", "")),
            poster=self._poster_url(movie.get("poster_path")),
            description=movie.get("overview", ""),
            release_info=release_date[:4] if release_date else "",
            imdb_rating=str(movie.get("vote_average", ""))
            if movie.get("vote_average")
            else "",
        )

    def _tv_to_preview(self, show: dict[str, Any], imdb_id: str) -> StremioMetaPreview:
        first_air = show.get("first_air_date", "")
        return StremioMetaPreview(
            id=imdb_id,
            type="series",
            name=show.get("name", show.get("original_name", "")),
            poster=self._poster_url(show.get("poster_path")),
            description=show.get("overview", ""),
            release_info=first_air[:4] if first_air else "",
            imdb_rating=str(show.get("vote_average", ""))
            if show.get("vote_average")
            else "",
        )

    # ------------------------------------------------------------------
    # Public API (TmdbClientPort)
    # ------------------------------------------------------------------

    async def find_by_imdb_id(
        self, imdb_id: str, *, language: str = "de"
    ) -> dict[str, Any] | None:
        """Lookup TMDB entry by IMDb ID. Returns movie/TV metadata or None."""
        lang_tag = f"{language}-{language.upper()}" if len(language) == 2 else language
        cache_key = f"tmdb:find:{imdb_id}:{language}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(
            f"/find/{imdb_id}",
            lang=lang_tag,
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

    async def get_title_and_year(
        self, imdb_id: str, *, language: str = "de"
    ) -> TitleMatchInfo | None:
        """Get title and release year from TMDB /find endpoint.

        The primary title is the localised title for *language*.
        When the original title differs, it is included in *alt_titles*
        so the title matcher can also match against the original language.
        """
        result = await self.find_by_imdb_id(imdb_id, language=language)
        if result is None:
            return None
        title = result.get("title") or result.get("name")
        if not title:
            return None
        date_str = result.get("release_date") or result.get("first_air_date") or ""
        year = int(date_str[:4]) if len(date_str) >= 4 else None

        # Include original-language title as alternative when it differs
        original = result.get("original_title") or result.get("original_name") or ""
        alt_titles = [original] if original and original != title else []

        return TitleMatchInfo(title=title, year=year, alt_titles=alt_titles)

    async def get_title_by_tmdb_id(self, tmdb_id: int, media_type: str) -> str | None:
        """Get the German title for a TMDB numeric ID.

        Used for catalog items that were discovered via TMDB trending/search
        (which don't include IMDb IDs).

        Args:
            tmdb_id: TMDB numeric ID.
            media_type: "movie" or "series" (maps to TMDB "tv").

        Returns:
            German title or None if not found.
        """
        endpoint = "tv" if media_type == "series" else "movie"
        cache_key = f"tmdb:title:{endpoint}:{tmdb_id}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get(f"/{endpoint}/{tmdb_id}")
        if data is None:
            return None

        title = data.get("title") or data.get("name") or None
        if title:
            await self._cache.set(cache_key, title, ttl=_TTL_FIND)
        return title

    async def trending_movies(self, page: int = 1) -> list[StremioMetaPreview]:
        """Fetch trending movies (German locale)."""
        cache_key = f"tmdb:trending:movie:{page}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get("/trending/movie/week", page=page)
        if data is None:
            return []

        previews = await self._previews(
            data.get("results", []), "movie", self._movie_to_preview
        )
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

        previews = await self._previews(
            data.get("results", []), "tv", self._tv_to_preview
        )
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

        previews = await self._previews(
            data.get("results", []), "movie", self._movie_to_preview
        )
        await self._cache.set(cache_key, previews, ttl=_TTL_SEARCH)
        return previews

    async def search_tv(self, query: str, page: int = 1) -> list[StremioMetaPreview]:
        """Search TV shows by query (German locale)."""
        cache_key = f"tmdb:search:tv:{query}:{page}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._get("/search/tv", query=query, page=page)
        if data is None:
            return []

        previews = await self._previews(
            data.get("results", []), "tv", self._tv_to_preview
        )
        await self._cache.set(cache_key, previews, ttl=_TTL_SEARCH)
        return previews
