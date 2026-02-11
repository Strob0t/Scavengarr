"""IMDB Suggest API fallback — title resolution without an API key.

Uses Wikidata (free, no key) to obtain the German title when possible,
falling back to the English title from IMDB Suggest.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import StremioMetaPreview, TitleMatchInfo
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

_SUGGEST_URL = "https://v2.sg.media-imdb.com/suggestion/t/{imdb_id}.json"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_TTL_TITLE = 86_400  # 24 hours


class ImdbFallbackClient:
    """Title resolver using the free IMDB Suggest API.

    Implements ``TmdbClientPort`` so it can be used as a drop-in
    replacement when no TMDB API key is configured.  Only title
    resolution works — catalog and trending methods return empty lists.

    German titles are resolved via Wikidata (free, no API key needed)
    to improve title matching for German streaming plugins.
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

    async def _fetch_wikidata_german_title(self, imdb_id: str) -> str | None:
        """Resolve the German title via Wikidata (free, no API key).

        Two-step approach:
        1. Search for the Wikidata entity by IMDB ID (P345 property).
        2. Fetch the German label for that entity.

        Returns the German title or None if unavailable.
        """
        cache_key = f"wikidata:de:{imdb_id}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            # Step 1: Find Wikidata entity ID by IMDB ID
            resp = await self._http.get(
                _WIKIDATA_API,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": f"haswbstatement:P345={imdb_id}",
                    "format": "json",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

            search_results = data.get("query", {}).get("search", [])
            if not search_results:
                return None

            qid = search_results[0].get("title", "")
            if not qid:
                return None

            # Step 2: Get German label
            resp2 = await self._http.get(
                _WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": "labels",
                    "languages": "de",
                    "format": "json",
                },
                timeout=10.0,
            )
            resp2.raise_for_status()
            data2 = resp2.json()

            german_label = (
                data2.get("entities", {})
                .get(qid, {})
                .get("labels", {})
                .get("de", {})
                .get("value")
            )
            if german_label:
                await self._cache.set(cache_key, german_label, ttl=_TTL_TITLE)
                log.info(
                    "wikidata_german_title_resolved",
                    imdb_id=imdb_id,
                    qid=qid,
                    title=german_label,
                )
            return german_label
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            log.debug(
                "wikidata_german_title_failed",
                imdb_id=imdb_id,
                exc_info=True,
            )
            return None

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
        """Return the German title via Wikidata, falling back to IMDB (English)."""
        german, entry = await asyncio.gather(
            self._fetch_wikidata_german_title(imdb_id),
            self._fetch_suggest(imdb_id),
        )
        if german:
            return german
        if entry is None:
            return None
        title = entry.get("l")
        if title:
            log.info("imdb_title_resolved", imdb_id=imdb_id, title=title)
        return title or None

    async def get_title_and_year(self, imdb_id: str) -> TitleMatchInfo | None:
        """Return title + year from IMDB Suggest API + Wikidata German title.

        Runs IMDB Suggest and Wikidata lookups in parallel.  When the
        German title is available it becomes the primary title (used as
        search query for German plugins) with the English title as
        alt_title for cross-language matching.
        """
        entry, german_title = await asyncio.gather(
            self._fetch_suggest(imdb_id),
            self._fetch_wikidata_german_title(imdb_id),
        )
        if not entry or not entry.get("l"):
            return None

        english_title = entry["l"]
        year = entry.get("y") if isinstance(entry.get("y"), int) else None

        if german_title and german_title != english_title:
            log.info(
                "imdb_fallback_title_with_german",
                imdb_id=imdb_id,
                german=german_title,
                english=english_title,
            )
            return TitleMatchInfo(
                title=german_title,
                year=year,
                alt_titles=[english_title],
            )

        log.debug(
            "imdb_fallback_title_english_only",
            imdb_id=imdb_id,
            title=english_title,
        )
        return TitleMatchInfo(title=english_title, year=year)

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
