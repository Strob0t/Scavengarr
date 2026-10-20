"""Dynamic query pool builder for search probes.

Generates probe queries from the free IMDB Suggest API (no API key
required), rotated deterministically per ISO week.
"""

from __future__ import annotations

import json
import random
import string
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.scoring import AgeBucket
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

_SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/{letter}/{query}.json"
_CACHE_TTL = 86_400  # 24 hours
_TIMEOUT = 10.0
# No pool is built for this long after IMDb could not be reached (DNS,
# refused): the scheduler's tick would rebuild every cell's pool
_UNAVAILABLE_COOLDOWN_S = 600.0
# IMDb cannot be reached: the name does not resolve or the connection is
# refused (ConnectError), or no connection within the timeout, a resolver
# that does not answer included (ConnectTimeout: 10 s per request)
_UNREACHABLE = (httpx.ConnectError, httpx.ConnectTimeout)

# Letters/queries used to discover popular titles via IMDB Suggest.
# The Suggest API returns the most popular matches for a query prefix.
_PROBE_QUERIES: list[str] = [
    *list(string.ascii_lowercase),
    "the",
    "das",
    "der",
    "die",
    "star",
    "dark",
    "love",
    "game",
    "house",
    "black",
    "dead",
    "last",
    "night",
    "king",
]

# Small fallback pool if IMDB is unreachable.
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


def _date_range_y1_2() -> tuple[int, int]:
    """Year range for 1-2 years ago."""
    now = datetime.now(timezone.utc)
    return now.year - 2, now.year - 1


def _date_range_y5_10() -> tuple[int, int]:
    """Year range for 5-10 years ago."""
    now = datetime.now(timezone.utc)
    return now.year - 10, now.year - 5


def _year_in_range(year: int | None, lo: int, hi: int) -> bool:
    """Check if year falls within [lo, hi] inclusive."""
    if year is None:
        return False
    return lo <= year <= hi


class QueryPoolBuilder:
    """Builds probe query lists from the free IMDB Suggest API.

    Queries are cached for 24h and rotated deterministically per ISO week.
    Falls back to a bundled list if IMDB answers with errors. When IMDb
    cannot be reached at all (``_UNREACHABLE``: DNS, refused, no connection
    within the timeout), the
    build stops at that first request, ``query_pool_unavailable`` is
    logged once, and no queries are given, nor fetched, for
    ``_UNAVAILABLE_COOLDOWN_S``: probes would fail on the same network,
    and in a production outage of 33 h every scheduler tick rebuilt the
    pool of each of 108 cells with 10 requests, each logged with a
    traceback (287 000 records in 24 h).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        cache: CachePort,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http_client
        self._cache = cache
        self._clock = clock
        self._unavailable_until = 0.0
        # the last pool built per cache key, with the time it was built: a
        # cache that answers nothing (Redis down) made every scheduler tick
        # rebuild every cell's pool (114 builds in 6 minutes, 10 requests
        # each); a copy within the cache's TTL answers instead
        self._copies: dict[str, tuple[float, list[str]]] = {}

    async def get_queries(
        self,
        category: int,
        bucket: AgeBucket,
        count: int = 2,
    ) -> list[str]:
        """Return ``count`` titles for probing.

        Titles are rotated deterministically by ISO week number
        so different titles are probed each week. ``[]`` while IMDb was
        found unreachable within ``_UNAVAILABLE_COOLDOWN_S``.
        """
        if self._clock() < self._unavailable_until:
            return []
        try:
            pool = await self._get_pool(category, bucket)
        except _UNREACHABLE as exc:
            self._unavailable_until = self._clock() + _UNAVAILABLE_COOLDOWN_S
            log.warning(
                "query_pool_unavailable",
                error=type(exc).__name__,
                detail=str(exc),
                cooldown_seconds=int(_UNAVAILABLE_COOLDOWN_S),
            )
            return []
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
        """Fetch title pool, with 24h caching; when the cache has nothing,
        the last pool built for the cell within those 24 h (in memory)."""
        media = self._media_type(category)
        cache_key = f"querypool:{media}:{bucket}"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        copy = self._copies.get(cache_key)
        if copy is not None and self._clock() - copy[0] < _CACHE_TTL:
            return copy[1]
        titles = await self._fetch_pool(media, bucket)
        if titles:
            self._copies[cache_key] = (self._clock(), titles)
            await self._cache.set(cache_key, json.dumps(titles), ttl=_CACHE_TTL)
        return titles

    async def _fetch_pool(self, media: str, bucket: AgeBucket) -> list[str]:
        """Fetch title pool from IMDB Suggest, filtered by year range."""
        qid_filter = {"movie"} if media == "movie" else {"tvSeries", "tvMiniSeries"}

        if bucket == "current":
            now = datetime.now(timezone.utc)
            year_lo, year_hi = now.year - 1, now.year
        elif bucket == "y1_2":
            year_lo, year_hi = _date_range_y1_2()
        elif bucket == "y5_10":
            year_lo, year_hi = _date_range_y5_10()
        else:
            return []

        return await self._collect_titles(qid_filter, year_lo, year_hi)

    async def _collect_titles(
        self,
        qid_filter: set[str],
        year_lo: int,
        year_hi: int,
    ) -> list[str]:
        """Query IMDB Suggest with multiple prefixes and collect matching titles."""
        seen: set[str] = set()
        titles: list[str] = []

        # Use a deterministic subset of probe queries per week for variety.
        seed = _week_seed()
        rng = random.Random(seed)
        queries = _PROBE_QUERIES.copy()
        rng.shuffle(queries)
        # Use 10 queries per pool build — good balance of coverage vs speed.
        selected = queries[:10]

        for query in selected:
            entries = await self._fetch_suggest(query)
            for entry in entries:
                qid = entry.get("qid", "")
                if qid not in qid_filter:
                    continue
                year = entry.get("y") if isinstance(entry.get("y"), int) else None
                if not _year_in_range(year, year_lo, year_hi):
                    continue
                title = entry.get("l", "").strip()
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                titles.append(title)

        titles.sort(key=_title_key)
        log.info(
            "query_pool_built",
            source="imdb_suggest",
            count=len(titles),
            year_range=f"{year_lo}-{year_hi}",
        )
        return titles

    async def _fetch_suggest(self, query: str) -> list[dict[str, Any]]:
        """Fetch IMDB Suggest results for a query prefix; an
        ``_UNREACHABLE`` error goes to the caller (IMDb cannot be reached,
        the other prefixes would fail the same way)."""
        letter = query[0] if query[0].isalpha() else "a"
        url = _SUGGEST_URL.format(letter=letter, query=query)
        try:
            resp = await self._http.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data.get("d", [])
        except _UNREACHABLE:
            raise
        except (httpx.HTTPError, ValueError):
            log.warning("query_pool_imdb_error", query=query, exc_info=True)
            return []

    @staticmethod
    def _media_type(category: int) -> str:
        """Map Torznab category to IMDB media type."""
        if 5000 <= category < 6000:
            return "tv"
        return "movie"

    @staticmethod
    def _fallback_pool(category: int) -> list[str]:
        """Small bundled fallback if IMDB is unreachable."""
        if 5000 <= category < 6000:
            return _FALLBACK_TV.copy()
        return _FALLBACK_MOVIES.copy()
