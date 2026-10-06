"""The titles a Stremio request is searched and matched with.

TMDB names the requested title in each language the plugins search in.
Plugins with the same languages search together, with one reference title
built from those languages' titles, and their results are matched against
it (``filter_fn``, the title matcher with its thresholds).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from scavengarr.domain.entities.stremio import StremioStreamRequest, TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.tmdb import TmdbClientPort

# The title matcher: (results, reference, threshold, **weights) -> matches
TitleFilterFn = Callable[..., list[SearchResult]]


class TitleMatchConfig(Protocol):
    """The title matcher's threshold and weights."""

    title_match_threshold: float
    title_year_bonus: float
    title_year_penalty: float
    title_sequel_penalty: float
    title_extra_words_penalty: float
    title_year_tolerance_movie: int
    title_year_tolerance_series: int


class TitleResolver:
    """A request's title per language, and which results match it."""

    def __init__(
        self,
        *,
        tmdb: TmdbClientPort,
        plugins: PluginRegistryPort,
        filter_fn: TitleFilterFn,
        config: TitleMatchConfig,
    ) -> None:
        self._tmdb = tmdb
        self._plugins = plugins
        self._filter_fn = filter_fn
        self._config = config

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
    ) -> dict[str, TitleMatchInfo | None]:
        """Fetch title info for each language in parallel.

        Returns a dict mapping language code to TitleMatchInfo (or None).
        For ``tmdb:`` prefixed IDs (no language variants), the same
        result is returned for every language.
        """
        infos = await asyncio.gather(
            *(self._title_info(request, language=lang) for lang in languages)
        )
        return dict(zip(languages, infos))

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
        config = self._config
        return await asyncio.to_thread(
            lambda: self._filter_fn(
                results,
                ref,
                config.title_match_threshold,
                year_bonus=config.title_year_bonus,
                year_penalty=config.title_year_penalty,
                sequel_penalty=config.title_sequel_penalty,
                extra_words_penalty=config.title_extra_words_penalty,
                year_tolerance_movie=config.title_year_tolerance_movie,
                year_tolerance_series=config.title_year_tolerance_series,
            ),
        )
