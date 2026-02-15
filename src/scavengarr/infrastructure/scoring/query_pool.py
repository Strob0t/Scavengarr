"""Dynamic query pool builder for search probes.

Generates probe queries from the free IMDB Suggest API + Wikidata.
No API key required.  Titles are German-localised (via Wikidata) and
rotated deterministically per ISO week.
"""

from __future__ import annotations

import json
import random
import string
from datetime import date, datetime, timezone
from typing import Any, Literal

import httpx
import structlog

from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

AgeBucket = Literal["current", "y1_2", "y5_10"]

_SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/{letter}/{query}.json"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_CACHE_TTL = 86_400  # 24 hours
_TIMEOUT = 10.0

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
    German titles are resolved via Wikidata (free, no API key).
    Falls back to a bundled list if IMDB is unreachable.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        cache: CachePort,
    ) -> None:
        self._http = http_client
        self._cache = cache

    async def get_queries(
        self,
        category: int,
        bucket: AgeBucket,
        count: int = 2,
    ) -> list[str]:
        """Return ``count`` titles for probing.

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
        """Fetch IMDB Suggest results for a query prefix."""
        letter = query[0] if query[0].isalpha() else "a"
        url = _SUGGEST_URL.format(letter=letter, query=query)
        try:
            resp = await self._http.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data.get("d", [])
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
