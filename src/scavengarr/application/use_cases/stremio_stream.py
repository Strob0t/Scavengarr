"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import replace
from typing import Any, Protocol

import structlog

from scavengarr.application.stremio.plugin_search import (
    BrowserWarmupFn,
    CircuitBreaker,
    EpisodeFilterFn,
    PluginHealth,
    PluginSearchRunner,
)
from scavengarr.application.stremio.plugin_selection import (
    PluginSelectionConfig,
    PluginSelector,
)
from scavengarr.application.stremio.queries import first_available_title
from scavengarr.application.stremio.resolution import (
    CachedResolutionCallback,
    ResolveCallback,
    ResolveConfig,
    ResolveFlow,
)
from scavengarr.application.stremio.search_cache import (
    SearchCache,
    search_cache_key,
)
from scavengarr.application.stremio.stream_builder import (
    apply_resolution,
    build_cache_link,
    build_stream_from_resolved,
    deduplicate_by_hoster,
    format_stream,
    stream_link_id,
)
from scavengarr.application.stremio.title_resolution import (
    TitleFilterFn,
    TitleMatchConfig,
    TitleResolver,
)
from scavengarr.application.stremio.title_search import TitleSearch
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StremioStream,
    StremioStreamRequest,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.concurrency import ConcurrencyPoolPort
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.plugin_score_store import PluginScoreStorePort
from scavengarr.domain.ports.search_engine import SearchEnginePort
from scavengarr.domain.ports.stream_link_repository import StreamLinkRepository
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, Stage, TelemetryPort
from scavengarr.domain.ports.tmdb import TmdbClientPort

# ---------------------------------------------------------------------------
# Protocols — define what this use case needs from its dependencies.
# Infrastructure components satisfy these via structural subtyping.
# ---------------------------------------------------------------------------


class _StremioConfig(TitleMatchConfig, PluginSelectionConfig, ResolveConfig, Protocol):
    """Configuration values consumed by StremioStreamUseCase."""

    plugin_timeout_seconds: float
    max_results_per_plugin: int


class _StreamSorter(Protocol):
    """Sorts RankedStreams by language, quality, and hoster scores."""

    def rank(self, stream: RankedStream) -> int: ...

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]: ...


# Type aliases for injected pure functions.
_ConvertFn = Callable[..., list[RankedStream]]

log = structlog.get_logger(__name__)


class StremioStreamUseCase:
    """Resolve Stremio stream requests into sorted stream links.

    Flow:
        1. Resolve IMDb ID to German title via TMDB.
        2. Discover plugins that provide streams.
        3. Search all plugins in parallel (bounded concurrency), one
           search per title shared by its requests, cached.
        4. Convert and rank the results as they arrive.
        5. Resolve each hoster's best link meanwhile; answer once enough
           hosters resolved, when everything is done, or at the deadline.
        6. Format into StremioStream objects.
    """

    def __init__(
        self,
        *,
        tmdb: TmdbClientPort,
        plugins: PluginRegistryPort,
        search_engine: SearchEnginePort,
        config: _StremioConfig,
        sorter: _StreamSorter,
        convert_fn: _ConvertFn,
        filter_fn: TitleFilterFn,
        episode_filter_fn: EpisodeFilterFn,
        user_agent: str,
        max_results_var: ContextVar[int | None],
        stream_link_repo: StreamLinkRepository | None = None,
        resolve_fn: ResolveCallback | None = None,
        cached_resolution_fn: CachedResolutionCallback | None = None,
        telemetry: TelemetryPort = NO_TELEMETRY,
        score_store: PluginScoreStorePort | None = None,
        browser_warmup_fn: BrowserWarmupFn | None = None,
        pool: ConcurrencyPoolPort,
        circuit_breaker: CircuitBreaker | None = None,
        mirror_groups: Mapping[str, str] | None = None,
        plugin_health: PluginHealth | None = None,
        cache: CachePort | None = None,
        search_ttl_seconds: int = 0,
    ) -> None:
        self._plugins = plugins
        self._titles = TitleResolver(
            tmdb=tmdb, plugins=plugins, filter_fn=filter_fn, config=config
        )
        self._sorter = sorter
        self._convert_fn = convert_fn
        self._user_agent = user_agent
        self._title_search = TitleSearch(
            search_runner=PluginSearchRunner(
                plugins=plugins,
                search_engine=search_engine,
                episode_filter_fn=episode_filter_fn,
                max_results_var=max_results_var,
                plugin_timeout=config.plugin_timeout_seconds,
                max_results_per_plugin=config.max_results_per_plugin,
                telemetry=telemetry,
                circuit_breaker=circuit_breaker,
                browser_warmup_fn=browser_warmup_fn,
                mirror_groups=mirror_groups,
                plugin_health=plugin_health,
                score_store=score_store,
            ),
            titles=self._titles,
            search_cache=SearchCache(cache, ttl_seconds=search_ttl_seconds),
            pool=pool,
            telemetry=telemetry,
            plugin_timeout_s=config.plugin_timeout_seconds,
            spawn=self._spawn,
        )
        self._stream_link_repo = stream_link_repo
        self._resolve_fn = resolve_fn
        self._resolve_flow = ResolveFlow(
            rank=self._rank,
            cached_resolution_fn=cached_resolution_fn,
            telemetry=telemetry,
            config=config,
            spawn=self._spawn,
        )
        self._max_probe_count = config.max_probe_count
        self._deadline_s = config.stream_deadline_seconds
        # Every task that outlives a request (searches, background
        # resolutions), for aclose()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._telemetry = telemetry
        self._selector = PluginSelector(
            plugins=plugins, score_store=score_store, config=config
        )

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
        with self._telemetry.stage("stremio_request", source="none") as stage:
            stage.annotate(imdb_id=request.imdb_id, content_type=request.content_type)
            streams = await self._answer(request, base_url, stage)
            self._telemetry.record("stremio_streams", len(streams))
            return streams

    async def _answer(
        self, request: StremioStreamRequest, base_url: str, stage: Stage
    ) -> list[StremioStream]:
        """The streams for *request*; sets the request *stage*'s source and
        outcome."""
        started = time.monotonic()
        category = 2000 if request.content_type == "movie" else 5000

        all_names = self._selector.stream_plugins()
        if not all_names:
            log.warning("stremio_no_stream_plugins")
            stage.outcome = "no_plugins"
            return []

        with self._telemetry.stage("stremio_phase", phase="metadata") as metadata:
            # Scored plugin selection (when enabled and scores are available)
            selected = await self._selector.select(all_names, category)

            # --- Multi-language title resolution ---
            languages = self._titles.languages(selected)
            title_infos = await self._titles.title_infos(request, languages)

            primary_title_info = first_available_title(title_infos, languages)
            metadata.outcome = "not_found" if primary_title_info is None else "found"
        if primary_title_info is None:
            log.warning("stremio_title_not_found", imdb_id=request.imdb_id)
            stage.outcome = "no_title"
            return []

        # --- Per-language-group search + filter (cached per title) ---
        key = search_cache_key(request)
        progress, source = await self._title_search.progress(
            key,
            request,
            self._titles.language_groups(selected),
            title_infos,
            category,
            started=started,
            scored=len(selected) < len(all_names),
        )
        stage.label(source=source)

        # Cached results can come from plugins this request did not select
        plugin_languages: dict[str, str] = {
            name: langs[0]
            for name in all_names
            if (langs := self._plugins.get_languages(name))
        }

        deadline = started + self._deadline_s
        served_here = self._stream_link_repo is not None and bool(base_url)
        resolved: dict[int, ResolvedStream] = {}
        if served_here and self._resolve_fn is not None:
            ranked, resolved = await self._resolve_flow.resolve(
                progress,
                plugin_languages,
                self._resolve_fn,
                deadline=deadline,
                key=key,
                from_cache=source in ("cache", "stale"),
            )
            ranked, resolved = self._with_measurements(ranked, resolved)
        else:
            await progress.wait(deadline)
            ranked = await self._rank(progress.results, plugin_languages)
            if self._resolve_fn is None:
                ranked = deduplicate_by_hoster(ranked)
            else:
                ranked = ranked[: self._max_probe_count]

        if not progress.results:
            if progress.total:
                log.info(
                    "stremio_all_filtered",
                    imdb_id=request.imdb_id,
                    total=progress.total,
                    search_done=progress.done,
                )
            else:
                log.info(
                    "stremio_search_no_results",
                    imdb_id=request.imdb_id,
                    search_done=progress.done,
                )
            stage.outcome = "empty"
            return []

        streams = [
            format_stream(
                s,
                reference_title=primary_title_info.title,
                year=primary_title_info.year,
                season=request.season,
                episode=request.episode,
            )
            for s in ranked
        ]
        if served_here:
            streams = await self._cache_and_proxy(streams, ranked, resolved, base_url)

        log.info(
            "stremio_search_complete",
            imdb_id=request.imdb_id,
            result_count=progress.total,
            filtered_count=len(progress.results),
            stream_count=len(streams),
        )
        stage.outcome = "streams" if streams else "empty"
        return streams

    def _spawn[T](self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Run *coro* as a task that may outlive the request (see aclose)."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            log.warning("stremio_background_search_failed", exc_info=error)

    async def aclose(self) -> None:
        """Cancel the searches and background resolutions still running."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _cache_and_proxy(
        self,
        streams: list[StremioStream],
        ranked: list[RankedStream],
        resolved_map: dict[int, ResolvedStream],
        base_url: str,
    ) -> list[StremioStream]:
        """Point the streams at Scavengarr and save the links it looks up.

        With a resolve callback, the streams in *resolved_map* (by index)
        go through ``/play/`` or, for HLS, the proxy
        (``build_stream_from_resolved``); the others are dropped. Without
        one, every stream goes through ``/play/``. Each answered stream
        gets its link saved; one save per ranked stream (dozens) delayed
        the answer by seconds.
        """
        answer: list[tuple[StremioStream, CachedStreamLink]] = []
        skipped_echo = 0
        skipped_unresolved = 0
        has_resolver = bool(self._resolve_fn)
        for i, stream in enumerate(streams):
            sid = stream_link_id(ranked[i].url)
            resolved = resolved_map.get(i)
            if resolved is not None:
                built = build_stream_from_resolved(
                    stream, resolved, ranked[i].url, sid, base_url, self._user_agent
                )
                if built is None:
                    skipped_echo += 1
                    continue
            elif has_resolver:
                # Resolver is configured but returned None — skip this stream.
                # The /play/ proxy would also fail (502).
                skipped_unresolved += 1
                continue
            else:
                # No resolver configured — proxy through /play/ endpoint
                built = replace(stream, url=f"{base_url}/api/v1/stremio/play/{sid}")
            answer.append((built, build_cache_link(sid, ranked[i], resolved)))

        unsaved = await self._save_links([lnk for _, lnk in answer])
        proxied = [s for s, lnk in answer if lnk.stream_id not in unsaved]
        skipped_unsaved = len(answer) - len(proxied)
        if skipped_echo or skipped_unresolved or skipped_unsaved:
            log.info(
                "stremio_streams_skipped",
                skipped_echo=skipped_echo,
                skipped_unresolved=skipped_unresolved,
                skipped_unsaved=skipped_unsaved,
            )
        return proxied

    def _with_measurements(
        self, ranked: list[RankedStream], resolved: dict[int, ResolvedStream]
    ) -> tuple[list[RankedStream], dict[int, ResolvedStream]]:
        """The streams with what their resolutions measured (quality, size;
        ``apply_resolution``) and the resolutions by index.

        A changed quality changes the rank: the streams are sorted again
        (stable, like the sorter), the resolutions follow their streams.
        """
        merged = [
            apply_resolution(s, resolved[i]) if i in resolved else s
            for i, s in enumerate(ranked)
        ]
        if all(m.quality is s.quality for m, s in zip(merged, ranked, strict=True)):
            return merged, resolved
        scores = [self._sorter.rank(s) for s in merged]
        order = sorted(range(len(merged)), key=scores.__getitem__, reverse=True)
        return (
            [replace(merged[old], rank_score=scores[old]) for old in order],
            {new: resolved[old] for new, old in enumerate(order) if old in resolved},
        )

    async def _save_links(self, links: list[CachedStreamLink]) -> set[str]:
        """Save the links in parallel; return the stream ids not saved."""
        assert self._stream_link_repo is not None
        outcomes = await asyncio.gather(
            *(self._stream_link_repo.save(lnk) for lnk in links),
            return_exceptions=True,
        )
        errors = [
            (lnk.stream_id, outcome)
            for lnk, outcome in zip(links, outcomes, strict=True)
            if isinstance(outcome, BaseException)
        ]
        if errors:
            log.warning(
                "stremio_stream_link_save_failed",
                count=len(errors),
                error=str(errors[0][1]),
            )
        return {sid for sid, _ in errors}

    async def _rank(
        self, results: list[SearchResult], plugin_languages: dict[str, str]
    ) -> list[RankedStream]:
        """The streams of *results*, best first."""
        return self._sorter.sort(await self._convert(results, plugin_languages))

    async def _convert(
        self, results: list[SearchResult], plugin_languages: dict[str, str]
    ) -> list[RankedStream]:
        """The streams of *results* (in a worker thread: CPU work)."""
        return await asyncio.to_thread(
            lambda: self._convert_fn(results, plugin_languages=plugin_languages)
        )
