"""Fribb's anime-lists: the public map of anime ids, for the addon's outages.

``anime-list-full.json`` (about 7.5 MB) holds one record per AniDB entry with
the ids of a dozen sites. The records with a Kitsu id and an IMDb id are
reduced to the IMDb id, the type, TheTVDB's season and episode offset (a
split cour's episode 3 is S3E15; long-runners carry no season), held in
memory and cached 7 days in the cache backend. Loaded on the first lookup;
a failed download is retried after an hour at the earliest. The reverse
look-up for IMDb requests (the series entries without a season per IMDb id)
never waits: it starts the load in the background and answers once the list
is in memory.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import StremioContentType
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

LIST_URL = (
    "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
)
_CACHE_KEY = "anime_ids:lists:v1"
_TIMEOUT_S = 60.0
_TTL_LIST = 7 * 86_400
_RETRY_AFTER_S = 3600.0


@dataclass(frozen=True)
class ListEntry:
    """A Kitsu entry's IMDb id and where it sits in the IMDb series."""

    imdb_id: str
    content_type: StremioContentType
    season: int | None  # None: a long-runner or a one-season series
    episode_offset: int


class AnimeIdLists:
    """The list's entries by Kitsu id, loaded once and cached a week."""

    def __init__(
        self, *, http_client: httpx.AsyncClient, cache: CachePort, url: str = LIST_URL
    ) -> None:
        self._http = http_client
        self._cache = cache
        self._url = url
        self._entries: dict[int, ListEntry] | None = None
        # The Kitsu ids of the series entries without a season per IMDb id
        self._whole_series: dict[str, list[int]] | None = None
        self._expires_at = 0.0
        self._failed_at: float | None = None
        self._loading = asyncio.Lock()
        self._background: asyncio.Task[Any] | None = None

    async def entry(self, kitsu_id: int) -> ListEntry | None:
        """The entry for *kitsu_id*; ``None`` when unlisted or the list is
        unavailable."""
        entries = await self._load()
        return entries.get(kitsu_id) if entries else None

    def whole_series(self, imdb_id: str) -> list[int] | None:
        """The Kitsu ids of *imdb_id*'s series entries without a TheTVDB
        season (a long-runner's entry counts the series from its start),
        in id order; ``None`` while the list is not in memory. Never waits:
        a missing or stale list is loaded in the background, one load at a
        time, and a stale one answers until the load replaces it."""
        if (
            not self._fresh()
            and not self._waiting_after_failure()
            and (self._background is None or self._background.done())
        ):
            self._background = asyncio.create_task(self._load())
            self._background.add_done_callback(_load_done)
        if self._whole_series is None:
            return None
        return self._whole_series.get(imdb_id, [])

    async def aclose(self) -> None:
        """End a background load still running (at shutdown)."""
        if self._background is not None:
            self._background.cancel()
            await asyncio.gather(self._background, return_exceptions=True)

    def _fresh(self) -> bool:
        return self._entries is not None and time.monotonic() < self._expires_at

    def _waiting_after_failure(self) -> bool:
        return (
            self._failed_at is not None
            and time.monotonic() - self._failed_at < _RETRY_AFTER_S
        )

    async def _load(self) -> dict[int, ListEntry] | None:
        if self._fresh():
            return self._entries
        async with self._loading:
            if self._fresh():
                return self._entries
            if self._waiting_after_failure():
                return None
            cached = await self._cache.get(_CACHE_KEY)
            if cached is None:
                cached = await self._download()
                if cached is None:
                    self._failed_at = time.monotonic()
                    return None
                await self._cache.set(_CACHE_KEY, cached, ttl=_TTL_LIST)
            self._entries = _from_cached(cached)
            self._whole_series = _whole_series_index(self._entries)
            self._expires_at = time.monotonic() + _TTL_LIST
            self._failed_at = None
            return self._entries

    async def _download(self) -> dict[str, list[Any]] | None:
        """The reduced list, one attempt; ``None`` on any failure."""
        try:
            resp = await self._http.get(self._url, timeout=_TIMEOUT_S)
            resp.raise_for_status()
            # 7.5 MB of JSON: parsed and reduced off the event loop
            reduced = await asyncio.to_thread(_reduce, resp.content)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            log.warning("anime_id_lists_failed", reason=type(error).__name__)
            return None
        log.info("anime_id_lists_loaded", entries=len(reduced))
        return reduced


def _reduce(raw: bytes) -> dict[str, list[Any]]:
    """``{kitsu id: [imdb id, type, tvdb season, tvdb episode offset]}`` of
    the records with both ids."""
    reduced: dict[str, list[Any]] = {}
    for record in json.loads(raw):
        kitsu_id = record.get("kitsu_id")
        imdb_ids = record.get("imdb_id") or []
        imdb_id = imdb_ids if isinstance(imdb_ids, str) else next(iter(imdb_ids), "")
        if not isinstance(kitsu_id, int) or not imdb_id:
            continue
        reduced[str(kitsu_id)] = [
            imdb_id,
            "movie" if record.get("type") == "MOVIE" else "series",
            (record.get("season") or {}).get("tvdb"),
            (record.get("episode_offset") or {}).get("tvdb") or 0,
        ]
    return reduced


def _from_cached(data: dict[str, list[Any]]) -> dict[int, ListEntry]:
    return {
        int(kitsu_id): ListEntry(
            imdb_id=imdb_id,
            content_type="movie" if kind == "movie" else "series",
            season=season,
            episode_offset=offset,
        )
        for kitsu_id, (imdb_id, kind, season, offset) in data.items()
    }


def _whole_series_index(entries: dict[int, ListEntry]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for kitsu_id, entry in sorted(entries.items()):
        if entry.content_type == "series" and entry.season is None:
            index.setdefault(entry.imdb_id, []).append(kitsu_id)
    return index


def _load_done(task: asyncio.Task[Any]) -> None:
    """Log a background load that failed unexpectedly (the download logs its
    own failures)."""
    if not task.cancelled() and (error := task.exception()) is not None:
        log.warning("anime_id_lists_failed", reason=type(error).__name__)
