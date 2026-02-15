"""Dynamic query pool builder for search probes.

Generates probe queries from TMDB trending/discover endpoints.
Titles are German-localised and rotated deterministically per ISO week.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timezone
from typing import Literal

import httpx
import structlog

from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

AgeBucket = Literal["current", "y1_2", "y5_10"]

_BASE_URL = "https://api.themoviedb.org/3"
_CACHE_TTL = 86_400  # 24 hours

# Small fallback pool if TMDB is unreachable.
_FALLBACK_MOVIES: list[str] = [
    "Iron Man",
    "Der Pate",
    "Inception",
    "Interstellar",
    "Matrix",
    "Der Herr der Ringe",
    "Gladiator",
    "Joker",
    "Dune",
    "Avatar",
]
_FALLBACK_TV: list[str] = [
    "Breaking Bad",
    "Game of Thrones",
    "Stranger Things",
    "Dark",
    "Haus des Geldes",
    "The Witcher",
    "Peaky Blinders",
    "Better Call Saul",
    "Squid Game",
    "Wednesday",
]


def _title_key(title: str) -> str:
    """Stable sort key for deterministic ordering."""
    return title.lower().strip()


def _week_seed() -> int:
    """ISO week number as deterministic rotation seed."""
    today = date.today()
    return today.isocalendar()[1]


def _date_range_y1_2() -> tuple[str, str]:
    """Date range for 1–2 years ago."""
    now = datetime.now(timezone.utc)
    gte = f"{now.year - 2}-01-01"
    lte = f"{now.year - 1}-12-31"
    return gte, lte


def _date_range_y5_10() -> tuple[str, str]:
    """Date range for 5–10 years ago."""
    now = datetime.now(timezone.utc)
    gte = f"{now.year - 10}-01-01"
    lte = f"{now.year - 5}-12-31"
    return gte, lte


class QueryPoolBuilder:
    """Builds probe query lists from TMDB trending/discover endpoints.

    Queries are cached for 24h and rotated deterministically per ISO week.
    Falls back to a bundled list if TMDB is unreachable.
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

    async def get_queries(
        self,
        category: int,
        bucket: AgeBucket,
        count: int = 2,
    ) -> list[str]:
        """Return ``count`` German titles for probing.

        Titles are rotated deterministically by ISO week number
        so different titles are probed each week.
        """
        pool = await self._get_pool(category, bucket)
        if not pool:
            pool = self._fallback_pool(category)

        if not pool:
            return []

        # Deterministic shuffle per week.
        seed = _week_seed()
        rng = random.Random(seed)
        shuffled = pool.copy()
        rng.shuffle(shuffled)
        return shuffled[:count]

    async def _get_pool(self, category: int, bucket: AgeBucket) -> list[str]:
        """Fetch title pool, with 24h caching."""
        media = self._media_type(category)
        cache_key = f"querypool:{media}:{bucket}"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        titles = await self._fetch_pool(media, bucket)
        if titles:
            await self._cache.set(cache_key, json.dumps(titles), ttl=_CACHE_TTL)
        return titles

    async def _fetch_pool(self, media: str, bucket: AgeBucket) -> list[str]:
        """Fetch full title pool from TMDB."""
        if bucket == "current":
            return await self._fetch_trending(media)
        if bucket == "y1_2":
            gte, lte = _date_range_y1_2()
            return await self._fetch_discover(media, gte, lte)
        if bucket == "y5_10":
            gte, lte = _date_range_y5_10()
            return await self._fetch_discover(media, gte, lte)
        return []

    async def _fetch_trending(self, media: str) -> list[str]:
        """Fetch trending titles from TMDB."""
        data = await self._get(f"/trending/{media}/week")
        if data is None:
            return []
        return self._extract_titles(data.get("results", []), media)

    async def _fetch_discover(self, media: str, gte: str, lte: str) -> list[str]:
        """Fetch discover titles from TMDB with date range filter."""
        date_field = "primary_release_date" if media == "movie" else "first_air_date"
        extra = {
            f"{date_field}.gte": gte,
            f"{date_field}.lte": lte,
            "sort_by": "popularity.desc",
        }
        data = await self._get(f"/discover/{media}", **extra)
        if data is None:
            return []
        return self._extract_titles(data.get("results", []), media)

    async def _get(self, path: str, **extra) -> dict | None:
        """GET TMDB endpoint with error handling."""
        url = f"{_BASE_URL}{path}"
        params = {
            "api_key": self._api_key,
            "language": "de-DE",
            **extra,
        }
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            log.warning("query_pool_tmdb_error", path=path, exc_info=True)
            return None

    @staticmethod
    def _extract_titles(results: list[dict], media: str) -> list[str]:
        """Extract German titles from TMDB result list."""
        titles: list[str] = []
        title_key = "title" if media == "movie" else "name"
        for item in results:
            title = item.get(title_key, "").strip()
            if title:
                titles.append(title)
        # Sort for deterministic base ordering.
        titles.sort(key=_title_key)
        return titles

    @staticmethod
    def _media_type(category: int) -> str:
        """Map Torznab category to TMDB media type."""
        if 5000 <= category < 6000:
            return "tv"
        return "movie"

    @staticmethod
    def _fallback_pool(category: int) -> list[str]:
        """Small bundled fallback if TMDB is unreachable."""
        if 5000 <= category < 6000:
            return _FALLBACK_TV.copy()
        return _FALLBACK_MOVIES.copy()
