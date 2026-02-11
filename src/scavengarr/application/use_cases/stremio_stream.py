"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio

import structlog

from scavengarr.domain.entities.stremio import (
    RankedStream,
    StremioStream,
    StremioStreamRequest,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.search_engine import SearchEnginePort
from scavengarr.domain.ports.tmdb import TmdbClientPort
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter

log = structlog.get_logger(__name__)


def _format_stream(ranked: RankedStream) -> StremioStream:
    """Convert a scored RankedStream into Stremio protocol format."""
    quality_label = ranked.quality.name.replace("_", " ")
    name_parts = (
        [ranked.source_plugin, quality_label]
        if ranked.source_plugin
        else [quality_label]
    )
    name = " ".join(name_parts)

    desc_parts: list[str] = []
    if ranked.language:
        desc_parts.append(ranked.language.label)
    if ranked.hoster:
        desc_parts.append(ranked.hoster.upper())
    if ranked.size:
        desc_parts.append(ranked.size)

    return StremioStream(
        name=name,
        description=" | ".join(desc_parts) if desc_parts else "",
        url=ranked.url,
    )


def _build_search_query(
    title: str,
    request: StremioStreamRequest,
) -> str:
    """Build a search query string from title.

    Returns the plain title without SxxExx suffix — season and episode
    are passed as separate parameters to each plugin so they can
    navigate directly to the correct content.
    """
    return title


class StremioStreamUseCase:
    """Resolve Stremio stream requests into sorted stream links.

    Flow:
        1. Resolve IMDb ID to German title via TMDB.
        2. Discover plugins that provide streams.
        3. Search all plugins in parallel (bounded concurrency).
        4. Convert SearchResults to RankedStreams.
        5. Sort by language, quality, and hoster.
        6. Format into StremioStream objects.
    """

    def __init__(
        self,
        *,
        tmdb: TmdbClientPort,
        plugins: PluginRegistryPort,
        search_engine: SearchEnginePort,
        config: StremioConfig,
    ) -> None:
        self._tmdb = tmdb
        self._plugins = plugins
        self._search_engine = search_engine
        self._sorter = StreamSorter(config)
        self._max_concurrent = config.max_concurrent_plugins
        self._plugin_timeout = config.plugin_timeout_seconds

    async def execute(
        self,
        request: StremioStreamRequest,
    ) -> list[StremioStream]:
        """Resolve streams for a Stremio request.

        Args:
            request: Parsed stream request with IMDb ID and optional season/episode.

        Returns:
            Sorted list of StremioStream objects, best first.
            Empty list if title not found or no plugins match.
        """
        title = await self._resolve_title(request)
        if not title:
            log.warning("stremio_title_not_found", imdb_id=request.imdb_id)
            return []

        query = _build_search_query(title, request)
        category = 2000 if request.content_type == "movie" else 5000

        plugin_names = self._plugins.get_by_provides("stream")
        both_names = self._plugins.get_by_provides("both")
        all_names = sorted(set(plugin_names + both_names))

        if not all_names:
            log.warning("stremio_no_stream_plugins")
            return []

        log.info(
            "stremio_search_start",
            imdb_id=request.imdb_id,
            title=title,
            query=query,
            plugin_count=len(all_names),
        )

        all_results = await self._search_plugins(
            all_names,
            query,
            category,
            season=request.season,
            episode=request.episode,
        )

        if not all_results:
            log.info(
                "stremio_search_no_results",
                imdb_id=request.imdb_id,
                query=query,
            )
            return []

        plugin_languages: dict[str, str] = {}
        for name in all_names:
            try:
                plugin = self._plugins.get(name)
                lang = getattr(plugin, "default_language", None)
                if isinstance(lang, str):
                    plugin_languages[name] = lang
            except Exception:  # noqa: BLE001
                pass

        ranked = convert_search_results(all_results, plugin_languages=plugin_languages)
        sorted_streams = self._sorter.sort(ranked)

        streams = [_format_stream(s) for s in sorted_streams]

        log.info(
            "stremio_search_complete",
            imdb_id=request.imdb_id,
            query=query,
            result_count=len(all_results),
            stream_count=len(streams),
        )

        return streams

    async def _resolve_title(
        self,
        request: StremioStreamRequest,
    ) -> str | None:
        """Resolve a human-readable title from IMDb or TMDB ID via TMDB."""
        if request.imdb_id.startswith("tmdb:"):
            tmdb_id = request.imdb_id.removeprefix("tmdb:")
            return await self._tmdb.get_title_by_tmdb_id(
                int(tmdb_id), request.content_type
            )
        return await self._tmdb.get_german_title(request.imdb_id)

    async def _search_plugins(
        self,
        plugin_names: list[str],
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search all plugins in parallel with bounded concurrency."""
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _search_one(name: str) -> list[SearchResult]:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self._search_single_plugin(
                            name, query, category, season=season, episode=episode
                        ),
                        timeout=self._plugin_timeout,
                    )
                except TimeoutError:
                    log.warning(
                        "stremio_plugin_timeout",
                        plugin=name,
                        timeout=self._plugin_timeout,
                    )
                    return []

        tasks = [_search_one(name) for name in plugin_names]
        results_per_plugin = await asyncio.gather(*tasks)

        all_results: list[SearchResult] = []
        for results in results_per_plugin:
            all_results.extend(results)
        return all_results

    async def _search_single_plugin(
        self,
        name: str,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search a single plugin, catching and logging errors.

        Python plugins (with search() but no scraping stages) are called
        directly, then their results are validated via SearchEngine.
        YAML plugins (with scraping stages) are delegated to the SearchEngine.
        """
        try:
            plugin = self._plugins.get(name)
        except Exception:
            log.warning("stremio_plugin_not_found", plugin=name)
            return []

        try:
            if (
                hasattr(plugin, "search")
                and callable(plugin.search)
                and not hasattr(plugin, "scraping")
            ):
                # Python plugin: call directly, validate results
                raw = await plugin.search(
                    query, category=category, season=season, episode=episode
                )
                results = await self._search_engine.validate_results(raw)
            else:
                # YAML plugin: delegate to search engine
                results = await self._search_engine.search(
                    plugin, query, category=category
                )
        except Exception:
            log.warning("stremio_plugin_search_error", plugin=name, exc_info=True)
            return []

        # Tag results with source plugin for downstream use
        for r in results:
            if isinstance(r, SearchResult) and not r.metadata.get("source_plugin"):
                r.metadata["source_plugin"] = name

        log.debug(
            "stremio_plugin_search_done",
            plugin=name,
            result_count=len(results),
        )
        return results
