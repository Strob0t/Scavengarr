"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from uuid import uuid4

import structlog

from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.search_engine import SearchEnginePort
from scavengarr.domain.ports.stream_link_repository import StreamLinkRepository
from scavengarr.domain.ports.tmdb import TmdbClientPort
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.metrics import MetricsCollector
from scavengarr.infrastructure.plugins.constants import search_max_results
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match

log = structlog.get_logger(__name__)


def _format_stream(
    ranked: RankedStream,
    *,
    reference_title: str = "",
    year: int | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> StremioStream:
    """Convert a scored RankedStream into Stremio protocol format.

    When *reference_title* (from TMDB) is available it is always used as
    the stream name, enriched with year (movies) or season/episode (series).
    Falls back to release_name → ranked.title → source_plugin + quality.

    The description always starts with the source plugin name so that
    users can see which site the stream came from.
    """
    from scavengarr.domain.entities.stremio import StreamQuality

    quality_label = ranked.quality.name.replace("_", " ")
    show_quality = ranked.quality != StreamQuality.UNKNOWN

    # --- Build name (reference title has priority) ---
    title = reference_title or ranked.title
    if title:
        if season is not None and episode is not None:
            name = f"{title} S{season:02d}E{episode:02d}"
        elif year:
            name = f"{title} ({year})"
        else:
            name = title
        if show_quality:
            name = f"{name} {quality_label}"
    elif ranked.release_name:
        name = ranked.release_name
    else:
        name = (
            f"{ranked.source_plugin} {quality_label}"
            if ranked.source_plugin
            else quality_label
        )

    # --- Build description (source plugin always first) ---
    desc_parts: list[str] = []
    if ranked.source_plugin:
        desc_parts.append(ranked.source_plugin)
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


# Callback type for probing hoster URLs at /stream time.
# Accepts list of (index, url) tuples, returns set of alive indices.
ProbeCallback = Callable[[list[tuple[int, str]]], Awaitable[set[int]]]


class StremioStreamUseCase:
    """Resolve Stremio stream requests into sorted stream links.

    Flow:
        1. Resolve IMDb ID to German title via TMDB.
        2. Discover plugins that provide streams.
        3. Search all plugins in parallel (bounded concurrency).
        4. Convert SearchResults to RankedStreams.
        5. Sort by language, quality, and hoster.
        6. Probe hoster URLs to filter dead links (optional).
        7. Format into StremioStream objects.
    """

    def __init__(
        self,
        *,
        tmdb: TmdbClientPort,
        plugins: PluginRegistryPort,
        search_engine: SearchEnginePort,
        config: StremioConfig,
        stream_link_repo: StreamLinkRepository | None = None,
        probe_fn: ProbeCallback | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._tmdb = tmdb
        self._plugins = plugins
        self._search_engine = search_engine
        self._sorter = StreamSorter(config)
        self._max_concurrent = config.max_concurrent_plugins
        self._plugin_timeout = config.plugin_timeout_seconds
        self._title_match_threshold = config.title_match_threshold
        self._title_year_bonus = config.title_year_bonus
        self._title_year_penalty = config.title_year_penalty
        self._title_sequel_penalty = config.title_sequel_penalty
        self._title_year_tolerance_movie = config.title_year_tolerance_movie
        self._title_year_tolerance_series = config.title_year_tolerance_series
        self._max_results_per_plugin = config.max_results_per_plugin
        self._stream_link_repo = stream_link_repo
        self._probe_fn = probe_fn
        self._probe_at_stream_time = config.probe_at_stream_time
        self._max_probe_count = config.max_probe_count
        self._metrics = metrics

    async def execute(
        self,
        request: StremioStreamRequest,
        *,
        base_url: str = "",
    ) -> list[StremioStream]:
        """Resolve streams for a Stremio request.

        Args:
            request: Parsed stream request with IMDb ID and optional season/episode.
            base_url: Service base URL for generating proxy play links.

        Returns:
            Sorted list of StremioStream objects, best first.
            Empty list if title not found or no plugins match.
        """
        title_info = await self._resolve_title_info(request)
        title = title_info.title if title_info else None
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

        # --- title-match filtering ---
        filtered = filter_by_title_match(
            all_results,
            title_info,
            self._title_match_threshold,
            year_bonus=self._title_year_bonus,
            year_penalty=self._title_year_penalty,
            sequel_penalty=self._title_sequel_penalty,
            year_tolerance_movie=self._title_year_tolerance_movie,
            year_tolerance_series=self._title_year_tolerance_series,
        )

        if not filtered:
            log.info(
                "stremio_all_filtered",
                imdb_id=request.imdb_id,
                query=query,
                total=len(all_results),
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

        ranked = convert_search_results(filtered, plugin_languages=plugin_languages)
        sorted_streams = self._sorter.sort(ranked)

        streams = [
            _format_stream(
                s,
                reference_title=title_info.title if title_info else "",
                year=title_info.year if title_info else None,
                season=request.season,
                episode=request.episode,
            )
            for s in sorted_streams
        ]

        # Cache hoster URLs and replace with proxy play links
        if self._stream_link_repo and base_url:
            streams = await self._cache_and_proxy(streams, sorted_streams, base_url)

        log.info(
            "stremio_search_complete",
            imdb_id=request.imdb_id,
            query=query,
            result_count=len(all_results),
            filtered_count=len(filtered),
            stream_count=len(streams),
        )

        return streams

    async def _cache_and_proxy(
        self,
        streams: list[StremioStream],
        ranked: list[RankedStream],
        base_url: str,
    ) -> list[StremioStream]:
        """Cache hoster URLs and replace stream URLs with proxy play links.

        When a probe callback is configured and enabled, performs a
        lightweight GET probe on each hoster embed URL to filter dead
        links before caching. Only the top ``max_probe_count`` streams
        are probed; the rest pass through unchecked.
        """
        # --- Probe step: filter dead links ---
        if self._probe_fn and self._probe_at_stream_time:
            limit = min(len(ranked), self._max_probe_count)
            probe_targets = [(i, ranked[i].url) for i in range(limit)]
            t0_probe = time.perf_counter_ns()
            alive_indices = await self._probe_fn(probe_targets)
            probe_duration = time.perf_counter_ns() - t0_probe

            dead = limit - len(alive_indices)
            log.info(
                "stremio_probe_complete",
                total=limit,
                alive=len(alive_indices),
                filtered=dead,
            )
            if self._metrics is not None:
                self._metrics.record_probe(
                    total=limit,
                    alive=len(alive_indices),
                    dead=dead,
                    cf_blocked=0,
                    duration_ns=probe_duration,
                )

            # Keep only alive streams (preserve order); unprobed streams pass through
            streams = [
                s for i, s in enumerate(streams) if i in alive_indices or i >= limit
            ]
            ranked = [
                r for i, r in enumerate(ranked) if i in alive_indices or i >= limit
            ]

        # --- Cache step ---
        proxied: list[StremioStream] = []
        for stream, ranked_s in zip(streams, ranked):
            stream_id = uuid4().hex
            link = CachedStreamLink(
                stream_id=stream_id,
                hoster_url=ranked_s.url,
                title=ranked_s.title,
                hoster=ranked_s.hoster,
            )
            await self._stream_link_repo.save(link)
            proxy_url = f"{base_url}/api/v1/stremio/play/{stream_id}"
            proxied.append(
                StremioStream(
                    name=stream.name,
                    description=stream.description,
                    url=proxy_url,
                )
            )
        return proxied

    async def _resolve_title_info(
        self,
        request: StremioStreamRequest,
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
        info = await self._tmdb.get_title_and_year(request.imdb_id)
        if info is not None:
            return replace(info, content_type=request.content_type)
        return None

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

        t0 = time.perf_counter_ns()
        success = False
        try:
            if (
                hasattr(plugin, "search")
                and callable(plugin.search)
                and not hasattr(plugin, "scraping")
            ):
                # Python plugin: call directly, validate results
                # Set max_results context so plugins limit pagination
                token = search_max_results.set(self._max_results_per_plugin)
                try:
                    raw = await plugin.search(
                        query, category=category, season=season, episode=episode
                    )
                finally:
                    search_max_results.reset(token)
                results = await self._search_engine.validate_results(raw)
            else:
                # YAML plugin: delegate to search engine
                results = await self._search_engine.search(
                    plugin, query, category=category
                )
            success = True
        except Exception:
            log.warning("stremio_plugin_search_error", plugin=name, exc_info=True)
            results = []
        finally:
            duration_ns = time.perf_counter_ns() - t0
            if self._metrics is not None:
                self._metrics.record_plugin_search(
                    name,
                    duration_ns,
                    len(results),
                    success=success,
                )

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
