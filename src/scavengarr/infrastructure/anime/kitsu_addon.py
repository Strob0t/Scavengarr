"""The Anime Kitsu addon's record of a title: IMDb id and episode placement.

The addon (the Stremio addon whose catalogs hand out ``kitsu:`` ids) answers
``/meta/<type>/kitsu:<id>.json`` with the title's IMDb id and, per episode,
how IMDb counts it (``imdbSeason``, ``imdbEpisode``): a split cour's episode
3 is S3E15, One Piece's episode 1000 is S21E109. The answer holds every
episode (One Piece: 1,410 videos, about 400 KB), so it is reduced to a
record and cached 30 days. Cloudflare in front of the addon refuses httpx's
default User-Agent; the shared client sends Scavengarr's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from scavengarr.domain.entities.stremio import StremioContentType
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

ADDON_URL = "https://anime-kitsu.strem.fun"
_TIMEOUT_S = 5.0
_TTL_RECORD = 30 * 86_400  # the addon's mappings change seldom


@dataclass(frozen=True)
class Placement:
    """Where an episode sits: IMDb's numbering (``mapped``) or Kitsu's own."""

    season: int
    episode: int
    mapped: bool


@dataclass(frozen=True)
class AddonRecord:
    """A title's IMDb id (``""`` when the addon knows none) and its episodes
    by Kitsu's number."""

    imdb_id: str
    content_type: StremioContentType
    episodes: Mapping[int, Placement]


class KitsuAddonClient:
    """The addon's record per title and route type, cached 30 days."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        cache: CachePort,
        base_url: str = ADDON_URL,
    ) -> None:
        self._http = http_client
        self._cache = cache
        self._base_url = base_url

    async def cached(
        self, content_type: StremioContentType, kitsu_id: str
    ) -> AddonRecord | None:
        """The title's record from the cache, if any."""
        cached = await self._cache.get(_key(content_type, kitsu_id))
        return None if cached is None else _from_cached(cached)

    async def fetch(
        self, content_type: StremioContentType, kitsu_id: str
    ) -> AddonRecord | None:
        """The title's record from the addon (one attempt, cached 30 days);
        ``None`` when the addon does not answer."""
        meta = await self._fetch(content_type, kitsu_id)
        if meta is None:
            return None
        record = _reduce(meta)
        await self._cache.set(
            _key(content_type, kitsu_id), _to_cached(record), ttl=_TTL_RECORD
        )
        return record

    async def _fetch(
        self, content_type: StremioContentType, kitsu_id: str
    ) -> dict[str, Any] | None:
        """The addon's ``meta`` object, one attempt; ``None`` on any failure."""
        url = f"{self._base_url}/meta/{content_type}/kitsu:{kitsu_id}.json"
        try:
            resp = await self._http.get(url, timeout=_TIMEOUT_S)
            resp.raise_for_status()
            meta = resp.json().get("meta")
        except httpx.HTTPStatusError as error:
            log.warning(
                "anime_id_addon_failed",
                kitsu_id=kitsu_id,
                status=error.response.status_code,
            )
            return None
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            # No message: an httpx error names the URL
            log.warning(
                "anime_id_addon_failed",
                kitsu_id=kitsu_id,
                reason=type(error).__name__,
            )
            return None
        if not isinstance(meta, dict):
            log.warning("anime_id_addon_failed", kitsu_id=kitsu_id, reason="no_meta")
            return None
        return meta


def _key(content_type: StremioContentType, kitsu_id: str) -> str:
    return f"anime_ids:addon:v1:{content_type}:{kitsu_id}"


def _reduce(meta: dict[str, Any]) -> AddonRecord:
    episodes: dict[int, Placement] = {}
    for video in meta.get("videos") or []:
        number = video.get("episode")
        if not isinstance(number, int):
            continue
        season, episode = video.get("imdbSeason"), video.get("imdbEpisode")
        if isinstance(season, int) and isinstance(episode, int):
            episodes[number] = Placement(season, episode, mapped=True)
        elif isinstance(video.get("season"), int):
            episodes[number] = Placement(video["season"], number, mapped=False)
    return AddonRecord(
        imdb_id=str(meta.get("imdb_id") or ""),
        content_type="movie" if meta.get("type") == "movie" else "series",
        episodes=episodes,
    )


def _to_cached(record: AddonRecord) -> dict[str, Any]:
    return {
        "imdb_id": record.imdb_id,
        "type": record.content_type,
        "episodes": {
            str(number): [p.season, p.episode, p.mapped]
            for number, p in record.episodes.items()
        },
    }


def _from_cached(data: dict[str, Any]) -> AddonRecord:
    return AddonRecord(
        imdb_id=data["imdb_id"],
        content_type="movie" if data["type"] == "movie" else "series",
        episodes={
            int(number): Placement(season, episode, mapped=bool(mapped))
            for number, (season, episode, mapped) in data["episodes"].items()
        },
    )
