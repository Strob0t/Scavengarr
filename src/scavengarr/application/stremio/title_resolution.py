"""The titles a Stremio request is searched and matched with.

TMDB names the requested title in each language the plugins search in,
and the catalog (Cinemeta, ``SeriesMetaPort``) names its identity: the
IMDb id, whether its genres call it animation and, for a series, the
episode list the episode reference (``EpisodeRef``) is built from.
Plugins with the same languages search together, with one reference title
built from those languages' titles, and their results are matched against
it (``filter_fn``, the title matcher with its thresholds).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from scavengarr.domain.entities.stremio import (
    EpisodeRef,
    SeriesMeta,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.series_meta import NO_SERIES_META, SeriesMetaPort
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.domain.ports.tmdb import TmdbClientPort

# The title matcher: (results, reference, threshold, **weights) -> matches
TitleFilterFn = Callable[..., list[SearchResult]]


class TitleMatchConfig(Protocol):
    """The title matcher's threshold and weights."""

    title_match_threshold: float
    title_year_bonus: float
    title_sequel_penalty: float
    title_extra_words_penalty: float
    title_year_tolerance_movie: int
    title_year_tolerance_series: int


class TitleResolver:
    """A request's title per language, its catalog record, and which
    results match it."""

    def __init__(
        self,
        *,
        tmdb: TmdbClientPort,
        plugins: PluginRegistryPort,
        filter_fn: TitleFilterFn,
        config: TitleMatchConfig,
        series_meta: SeriesMetaPort = NO_SERIES_META,
        telemetry: TelemetryPort = NO_TELEMETRY,
    ) -> None:
        self._tmdb = tmdb
        self._plugins = plugins
        self._filter_fn = filter_fn
        self._series_meta = series_meta
        self._telemetry = telemetry
        self._threshold = config.title_match_threshold
        self._weights: dict[str, float] = {
            "year_bonus": config.title_year_bonus,
            "sequel_penalty": config.title_sequel_penalty,
            "extra_words_penalty": config.title_extra_words_penalty,
            "year_tolerance_movie": config.title_year_tolerance_movie,
            "year_tolerance_series": config.title_year_tolerance_series,
        }

    def default_languages(self, plugin_names: list[str]) -> dict[str, str]:
        """Each of *plugin_names*' first language: the language of its
        results that name none. Plugins without languages are left out."""
        return {
            name: langs[0]
            for name in plugin_names
            if (langs := self._plugins.get_languages(name))
        }

    def languages(self, plugin_names: list[str]) -> list[str]:
        """Every language *plugin_names* search in, sorted."""
        return sorted(
            {
                lang
                for name in plugin_names
                for lang in self._plugins.get_languages(name)
            }
        )

    def language_groups(
        self, plugin_names: list[str]
    ) -> dict[tuple[str, ...], list[str]]:
        """*plugin_names* grouped by their identical language lists."""
        groups: dict[tuple[str, ...], list[str]] = {}
        for name in plugin_names:
            key = tuple(self._plugins.get_languages(name))
            groups.setdefault(key, []).append(name)
        return groups

    async def title_infos(
        self,
        request: StremioStreamRequest,
        languages: list[str],
    ) -> tuple[dict[str, TitleMatchInfo | None], SeriesMeta | None]:
        """The title info per language (``None`` where the title is unknown)
        and the request's catalog record.

        The titles come from TMDB, one lookup per language in parallel (a
        ``tmdb:`` id has no language variants: the same info for every
        language); the record comes from the catalog, once per request, and
        gives every info its identity: the request's IMDb id and whether the
        genres name animation (``None`` without a record).
        """
        meta, infos = await asyncio.gather(
            self.series_meta(request),
            asyncio.gather(
                *(self._title_info(request, language=lang) for lang in languages)
            ),
        )
        imdb_id = request.imdb_id if request.imdb_id.startswith("tt") else None
        animation = None if meta is None else "Animation" in meta.genres
        return {
            lang: None
            if info is None
            else replace(info, imdb_id=imdb_id, animation=animation)
            for lang, info in zip(languages, infos)
        }, meta

    async def series_meta(self, request: StremioStreamRequest) -> SeriesMeta | None:
        """The request's catalog record, recorded as the ``series_meta`` phase
        (``found``, ``not_found``, ``error``); nothing for a ``tmdb:`` id,
        which the catalog does not know."""
        if not request.imdb_id.startswith("tt"):
            return None
        with self._telemetry.stage("stremio_phase", phase="series_meta") as stage:
            meta, stage.outcome = await self._series_meta.lookup(
                request.content_type, request.imdb_id
            )
        return meta

    @staticmethod
    def episode_ref(
        request: StremioStreamRequest,
        meta: SeriesMeta | None,
        *,
        absolute: int | None = None,
    ) -> EpisodeRef | None:
        """The episode *request* means, for plugins that locate episodes on
        the site's own numbering: the request's season and episode, the
        catalog entry's English title and release date, and the absolute
        number, *absolute* when given (a ``kitsu:`` request's episode
        number) else the entry's position among the regular seasons
        (season 1 and up) ordered by season and episode. ``None`` without a
        record or for a request without an episode."""
        if meta is None or request.season is None or request.episode is None:
            return None
        wanted = (request.season, request.episode)
        entry = next(
            (e for e in meta.episodes if (e.season, e.episode) == wanted), None
        )
        if absolute is None and entry is not None and entry.season >= 1:
            regular = sorted(
                (e for e in meta.episodes if e.season >= 1),
                key=lambda e: (e.season, e.episode),
            )
            absolute = regular.index(entry) + 1
        return EpisodeRef(
            season=request.season,
            episode=request.episode,
            title=entry.name or None if entry is not None else None,
            aired=entry.released if entry is not None else None,
            absolute=absolute,
        )

    async def _title_info(
        self,
        request: StremioStreamRequest,
        *,
        language: str = "de",
    ) -> TitleMatchInfo | None:
        """Resolve title + year from IMDb or TMDB ID for matching."""
        if request.imdb_id.startswith("tmdb:"):
            tmdb_id = request.imdb_id.removeprefix("tmdb:")
            title = await self._tmdb.get_title_by_tmdb_id(
                int(tmdb_id), request.content_type
            )
            if not title:
                return None
            return TitleMatchInfo(title=title, content_type=request.content_type)
        info = await self._tmdb.get_title_and_year(request.imdb_id, language=language)
        if info is not None:
            return replace(info, content_type=request.content_type)
        return None

    async def matching(
        self, results: list[SearchResult], ref: TitleMatchInfo
    ) -> list[SearchResult]:
        """The results whose title matches *ref* (in a worker thread: CPU work;
        ``to_thread`` keeps the log context, unlike ``run_in_executor``)."""
        if not results:
            return []
        return await asyncio.to_thread(
            lambda: self._filter_fn(results, ref, self._threshold, **self._weights)
        )
