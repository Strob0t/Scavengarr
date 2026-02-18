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
_SEARCH_URL = "https://v2.sg.media-imdb.com/suggestion/{letter}/{query}.json"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_TTL_TITLE = 86_400  # 24 hours
_TTL_SEARCH = 3_600  # 1 hour for search results


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

    async def _fetch_wikidata_title(
        self, imdb_id: str, language: str = "de"
    ) -> str | None:
        """Resolve a localised title via Wikidata (free, no API key).

        Two-step approach:
        1. Search for the Wikidata entity by IMDB ID (P345 property).
        2. Fetch the label for *language* on that entity.

        Returns the localised title or None if unavailable.
        """
        cache_key = f"wikidata:{language}:{imdb_id}"
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

            # Step 2: Get label in requested language
            resp2 = await self._http.get(
                _WIKIDATA_API,
                params={
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": "labels",
                    "languages": language,
                    "format": "json",
                },
                timeout=10.0,
            )
            resp2.raise_for_status()
            data2 = resp2.json()

            label = (
                data2.get("entities", {})
                .get(qid, {})
                .get("labels", {})
                .get(language, {})
                .get("value")
            )
            if label:
                await self._cache.set(cache_key, label, ttl=_TTL_TITLE)
                log.info(
                    "wikidata_title_resolved",
                    imdb_id=imdb_id,
                    language=language,
                    qid=qid,
                    title=label,
                )
            return label
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            log.debug(
                "wikidata_title_failed",
                imdb_id=imdb_id,
                language=language,
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

    async def get_title_and_year(
        self, imdb_id: str, *, language: str = "de"
    ) -> TitleMatchInfo | None:
        """Return title + year from IMDB Suggest API + Wikidata title.

        Runs IMDB Suggest (English) and Wikidata (requested *language*)
        lookups in parallel.

        - ``language="de"``: German primary, English alt (original behavior).
        - ``language="en"``: English primary from IMDB Suggest, no alt.
        - Other: Wikidata label primary, English alt.
        """
        entry, wikidata_title = await asyncio.gather(
            self._fetch_suggest(imdb_id),
            self._fetch_wikidata_title(imdb_id, language),
        )
        if not entry or not entry.get("l"):
            return None

        english_title = entry["l"]
        year = entry.get("y") if isinstance(entry.get("y"), int) else None

        if language == "en":
            # English requested — use IMDB Suggest title directly
            return TitleMatchInfo(title=english_title, year=year)

        if wikidata_title and wikidata_title != english_title:
            log.info(
                "imdb_fallback_title_with_localized",
                imdb_id=imdb_id,
                language=language,
                localized=wikidata_title,
                english=english_title,
            )
            return TitleMatchInfo(
                title=wikidata_title,
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
        """Not available without TMDB API — requires editorial curation."""
        return []

    async def trending_tv(self, page: int = 1) -> list[StremioMetaPreview]:
        """Not available without TMDB API — requires editorial curation."""
        return []

    async def search_movies(
        self, query: str, page: int = 1
    ) -> list[StremioMetaPreview]:
        """Search movies via IMDB Suggest API (free, no key)."""
        return await self._search_suggest(query, qid_filter={"movie"})

    async def search_tv(self, query: str, page: int = 1) -> list[StremioMetaPreview]:
        """Search TV shows via IMDB Suggest API (free, no key)."""
        return await self._search_suggest(
            query, qid_filter={"tvSeries", "tvMiniSeries"}
        )

    async def _search_suggest(
        self,
        query: str,
        qid_filter: set[str],
    ) -> list[StremioMetaPreview]:
        """Fetch IMDB Suggest search results and filter by content type.

        The IMDB Suggest API accepts a query via URL:
        ``https://v2.sg.media-imdb.com/suggestion/{first_letter}/{query}.json``

        Each result has a ``qid`` field (movie, tvSeries, tvMiniSeries, etc.)
        used to filter content type.
        """
        if not query or not query.strip():
            return []

        clean = query.strip().lower()
        cache_key = f"imdb:search:{clean}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            # Cached raw entries — filter by qid
            return self._entries_to_previews(cached, qid_filter)

        letter = clean[0] if clean[0].isalpha() else "a"
        url = _SEARCH_URL.format(letter=letter, query=clean)

        try:
            resp = await self._http.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            log.warning("imdb_search_failed", query=query, exc_info=True)
            return []

        entries = data.get("d", [])
        if not entries:
            return []

        await self._cache.set(cache_key, entries, ttl=_TTL_SEARCH)
        return self._entries_to_previews(entries, qid_filter)

    @staticmethod
    def _entries_to_previews(
        entries: list[dict[str, Any]],
        qid_filter: set[str],
    ) -> list[StremioMetaPreview]:
        """Convert IMDB Suggest entries to StremioMetaPreview list."""
        previews: list[StremioMetaPreview] = []
        for entry in entries:
            qid = entry.get("qid", "")
            if qid not in qid_filter:
                continue
            imdb_id = entry.get("id", "")
            if not imdb_id or not imdb_id.startswith("tt"):
                continue
            title = entry.get("l", "")
            if not title:
                continue

            year = entry.get("y")
            poster = entry.get("i", {}).get("imageUrl", "") if entry.get("i") else ""
            content_type = "movie" if qid == "movie" else "series"

            previews.append(
                StremioMetaPreview(
                    id=imdb_id,
                    type=content_type,
                    name=title,
                    poster=poster,
                    release_info=str(year) if year else "",
                )
            )
        return previews
