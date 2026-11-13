"""TMDB API client — async httpx implementation with caching."""

from __future__ import annotations

import asyncio
import re
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
_TTL_ALTERNATIVE = 7 * 86_400  # a title's alternatives rarely change
_TTL_ALTERNATIVE_FAILURE = 300  # retry a failed alternatives lookup after 5 min

_WORD_RE = re.compile(r"[a-z0-9]+")

# TMDB's alternative-title types whose title names the same content; a
# special, a sequel, a working title or an acronym is no search name.
_SEARCHABLE_ALTERNATIVE_TYPES = frozenset(
    {
        "",
        "romaji",
        "alternative spelling",
        "common misspelling",
        "literal translation",
        "original name",
        "title",
    }
)


def _title_words(text: str) -> list[str]:
    """Lower-case ASCII words of a title, in order."""
    return _WORD_RE.findall(text.lower())


def _is_searchable_shape(title: str) -> bool:
    """Whether *title* looks like a name, not an acronym or code.

    A lone word of five characters or fewer, a lone all-capitals word
    ("AGT", "ANTM-USA", "RPDR", "TWD", "SNK"), and a multi-word title made
    only of codes ("ANTM 2.0") are no names.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", title.strip())
    if not tokens:
        return False
    if len(tokens) == 1:
        return len(tokens[0]) > 5 and not tokens[0].isupper()
    return not all(token.isdigit() or token.isupper() for token in tokens)


def _searchable_alternative(
    entry: object,
    origin: set[str],
    localized: list[str],
) -> str | None:
    """The entry's title when it is a name worth searching with."""
    if not isinstance(entry, dict):
        return None
    alternative = entry.get("title")
    if not isinstance(alternative, str) or not alternative.strip():
        return None
    alternative = alternative.strip()
    kind = str(entry.get("type") or "").strip().lower()
    if kind not in _SEARCHABLE_ALTERNATIVE_TYPES:
        return None
    country = str(entry.get("iso_3166_1") or "").upper()
    if country not in origin:
        return None
    if not alternative.isascii() or not _is_searchable_shape(alternative):
        return None
    words = _title_words(alternative)
    if not words or set(words) <= set(localized):
        return None
    if localized and words[: len(localized)] == localized:
        return None
    return alternative


def _alternative_candidates(
    result: dict[str, Any],
    alternatives: list[dict[str, Any]],
    *,
    localized: list[str],
) -> list[str]:
    """The origin-country Latin-script alternatives worth searching with.

    A candidate that is the localized name, a shortened form of it, or
    the localized name with extra words (a spin-off) is not a second name
    and is left out, as is a title TMDB types as a special, sequel,
    working title or acronym and a short code shape.
    """
    origin = {str(code).upper() for code in (result.get("origin_country") or [])}
    if not origin:
        return []

    candidates: list[str] = []
    for entry in alternatives:
        alternative = _searchable_alternative(entry, origin, localized)
        if alternative is not None and alternative not in candidates:
            candidates.append(alternative)
    return candidates


def _best_prefix_candidate(candidates: list[str]) -> str | None:
    """The one candidate the others start with (the base title).

    The comparison is by words, so a colon-separated sequel counts. No
    candidate starts another, or two candidates tie for the most others,
    means the list is ambiguous and nothing is chosen.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    words = {candidate: _title_words(candidate) for candidate in candidates}

    def prefix_count(candidate: str) -> int:
        prefix = words[candidate]
        return sum(
            1
            for other in candidates
            if other != candidate
            and len(words[other]) > len(prefix)
            and words[other][: len(prefix)] == prefix
        )

    counts = [prefix_count(candidate) for candidate in candidates]
    best = max(counts)
    winners = [
        candidate
        for candidate, count in zip(candidates, counts, strict=True)
        if count == best
    ]
    return winners[0] if best > 0 and len(winners) == 1 else None


def _pick_search_alternative(
    result: dict[str, Any],
    alternatives: list[dict[str, Any]],
    *,
    title: str,
) -> str | None:
    """The original-language alternative to search with, if one is safe.

    A Japanese series' TMDB entry lists its title in romaji among the
    alternatives ("JoJo no Kimyou na Bouken"). German sites often index
    that name, and some (aniworld) cannot search a localized title with
    an apostrophe at all. The base romaji title is a prefix of its
    sequels and spin-offs, so the candidate that is a prefix of the most
    other candidates wins; with no such candidate a lone alternative is
    used, else nothing.
    """
    if not result.get("origin_country"):
        return None
    candidates = _alternative_candidates(
        result, alternatives, localized=_title_words(title)
    )
    return _best_prefix_candidate(candidates)


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
        except httpx.HTTPStatusError as exc:
            log.warning("tmdb_http_error", path=path, status=exc.response.status_code)
            return None
        except httpx.HTTPError as exc:
            log.warning("tmdb_network_error", path=path, error=type(exc).__name__)
            return None

    @staticmethod
    def _poster_url(poster_path: str | None) -> str:
        if not poster_path:
            return ""
        return f"{_POSTER_BASE}{poster_path}"

    @staticmethod
    def _endpoint(result: dict[str, Any]) -> str:
        """The TMDB endpoint a /find result belongs to ("tv" or "movie")."""
        media_type = result.get("media_type")
        if media_type in ("tv", "movie"):
            return str(media_type)
        if "name" in result or "first_air_date" in result or "original_name" in result:
            return "tv"
        return "movie"

    async def _alternative_titles(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        """TMDB's alternative titles of a title, cached by its TMDB id.

        A failed request is cached empty for five minutes and is no
        alternative: the title resolution goes on with the localized name.
        """
        tmdb_id = result.get("id")
        if not isinstance(tmdb_id, int):
            return []
        endpoint = self._endpoint(result)
        cache_key = f"tmdb:alternatives:{endpoint}:{tmdb_id}"
        cached = await self._cache.get(cache_key)
        if isinstance(cached, list):
            return cached

        data = await self._get(f"/{endpoint}/{tmdb_id}/alternative_titles")
        if data is None:
            await self._cache.set(cache_key, [], ttl=_TTL_ALTERNATIVE_FAILURE)
            return []
        results = data.get("results", [])
        if not isinstance(results, list):
            results = []
        titles = [entry for entry in results if isinstance(entry, dict)]
        await self._cache.set(cache_key, titles, ttl=_TTL_ALTERNATIVE)
        return titles

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

        A series whose origin country is known also gets a Latin-script
        original-language alternative (an anime's romaji title) in
        *alt_titles* and *search_titles*: sites often index that name,
        and some cannot search the localized one (aniworld and an
        apostrophe). A movie's /find result names no origin country, so
        its alternatives are left alone.
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

        search_titles: list[str] = []
        if result.get("origin_country"):
            alternatives = await self._alternative_titles(result)
            search_title = _pick_search_alternative(result, alternatives, title=title)
            if search_title:
                search_titles.append(search_title)
                if search_title != title and search_title not in alt_titles:
                    alt_titles.append(search_title)

        return TitleMatchInfo(
            title=title,
            year=year,
            alt_titles=alt_titles,
            search_titles=search_titles,
        )

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
