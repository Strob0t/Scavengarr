"""Cinemeta's record of a title: name, start year, genres and episodes.

Cinemeta (``v3-cinemeta.strem.io``) is the catalog Stremio reads the
request's ids from, so its meta names the title the request means: the
genres tell an anime from a live-action series of the same name, and the
episode list (IMDb's season and episode, the English name, the release
date) is the reference for sites that count episodes their own way. The
answer holds every episode (One Piece: 1,242 videos, 1.3 MB), so it is
reduced to a record and cached 7 days. An id the catalog does not know
(a 307 to the live catalog, which answers 404, or an answer without a
meta) is cached as "none" for a day. The shared client sends Scavengarr's
User-Agent.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import (
    EpisodeMeta,
    SeriesMeta,
    StremioContentType,
)
from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.series_meta import MetaOutcome

log = structlog.get_logger(__name__)

CINEMETA_URL = "https://v3-cinemeta.strem.io"
_TIMEOUT_S = 5.0
_TTL_RECORD = 7 * 86_400
_TTL_NONE = 86_400
_NONE = "none"
_YEAR_RE = re.compile(r"\d{4}")


class CinemetaClient:
    """A title's Cinemeta record per route type, cached 7 days."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        cache: CachePort,
        base_url: str = CINEMETA_URL,
    ) -> None:
        self._http = http_client
        self._cache = cache
        self._base_url = base_url

    async def meta(
        self, content_type: StremioContentType, imdb_id: str
    ) -> SeriesMeta | None:
        """The title's record; ``None`` when the catalog does not know the id
        or does not answer."""
        meta, _ = await self.lookup(content_type, imdb_id)
        return meta

    async def lookup(
        self, content_type: StremioContentType, imdb_id: str
    ) -> tuple[SeriesMeta | None, MetaOutcome]:
        """The title's record with the lookup's outcome: ``found``,
        ``not_found`` (the catalog does not know the id under that type: a
        404, or the ``{}`` the live catalog answers for a movie id asked as
        a series; remembered for a day) or ``error`` (no answer, logged;
        nothing cached)."""
        key = _key(content_type, imdb_id)
        cached = await self._cache.get(key)
        if cached == _NONE:
            return None, "not_found"
        if isinstance(cached, dict):
            return _from_cached(cached), "found"
        return await self._fetch(key, content_type, imdb_id)

    async def _fetch(
        self, key: str, content_type: StremioContentType, imdb_id: str
    ) -> tuple[SeriesMeta | None, MetaOutcome]:
        url = f"{self._base_url}/meta/{content_type}/{imdb_id}.json"
        try:
            resp = await self._http.get(url, timeout=_TIMEOUT_S, follow_redirects=True)
            if resp.status_code == 404:
                return await self._none(key)
            resp.raise_for_status()
            meta = resp.json().get("meta")
        except httpx.HTTPStatusError as error:
            log.warning(
                "cinemeta_failed",
                imdb_id=imdb_id,
                status=error.response.status_code,
            )
            return None, "error"
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            # No message: an httpx error names the URL
            log.warning("cinemeta_failed", imdb_id=imdb_id, reason=type(error).__name__)
            return None, "error"
        if not isinstance(meta, dict) or not meta:
            return await self._none(key)
        record = _reduce(meta)
        await self._cache.set(key, _to_cached(record), ttl=_TTL_RECORD)
        return record, "found"

    async def _none(self, key: str) -> tuple[None, MetaOutcome]:
        await self._cache.set(key, _NONE, ttl=_TTL_NONE)
        return None, "not_found"


def _key(content_type: StremioContentType, imdb_id: str) -> str:
    return f"cinemeta:v1:{content_type}:{imdb_id}"


def _reduce(meta: dict[str, Any]) -> SeriesMeta:
    episodes = tuple(
        EpisodeMeta(
            season=video["season"],
            episode=video["episode"],
            name=str(video.get("name") or ""),
            released=_date(video.get("released")),
        )
        for video in meta.get("videos") or []
        if isinstance(video, dict)
        and isinstance(video.get("season"), int)
        and isinstance(video.get("episode"), int)
    )
    return SeriesMeta(
        name=str(meta.get("name") or ""),
        year=_year(meta.get("year") or meta.get("releaseInfo")),
        genres=tuple(str(genre) for genre in meta.get("genres") or [] if genre),
        episodes=episodes,
    )


def _year(value: object) -> int | None:
    """The first year of ``"1999–"``, ``"2019–2024"`` or ``"2010"``."""
    found = _YEAR_RE.match(str(value or ""))
    return int(found.group()) if found else None


def _date(value: object) -> str | None:
    """The date of an ISO timestamp (``2001-03-21T14:15:00.000Z``)."""
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def _to_cached(record: SeriesMeta) -> dict[str, Any]:
    return {
        "name": record.name,
        "year": record.year,
        "genres": list(record.genres),
        "episodes": [
            [e.season, e.episode, e.name, e.released] for e in record.episodes
        ],
    }


def _from_cached(data: dict[str, Any]) -> SeriesMeta:
    return SeriesMeta(
        name=data["name"],
        year=data["year"],
        genres=tuple(data["genres"]),
        episodes=tuple(
            EpisodeMeta(season, episode, name, released)
            for season, episode, name, released in data["episodes"]
        ),
    )
