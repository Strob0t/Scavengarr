"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import random
import time
from collections.abc import Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import Any, Literal, Protocol

import structlog

from scavengarr.application.stremio.plugin_search import (
    BrowserWarmupFn,
    CircuitBreaker,
    EpisodeFilterFn,
    PluginHealth,
    PluginSearchRunner,
)
from scavengarr.application.stremio.queries import (
    build_lang_group_queries,
    build_multi_lang_reference,
    first_available_title,
)
from scavengarr.application.stremio.resolution import (
    HosterResolution,
    ResolveCallback,
)
from scavengarr.application.stremio.search_cache import (
    SearchCache,
    search_cache_key,
)
from scavengarr.application.stremio.search_progress import SearchProgress
from scavengarr.application.stremio.stream_builder import (
    build_cache_link,
    build_stream_from_resolved,
    deduplicate_by_hoster,
    format_stream,
    hoster_key,
    is_direct_video_url,
    stream_link_id,
)
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.concurrency import (
    ConcurrencyBudgetPort,
    ConcurrencyPoolPort,
)
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


class _StremioConfig(Protocol):
    """Configuration values consumed by StremioStreamUseCase."""

    plugin_timeout_seconds: float
    stream_deadline_seconds: float
    title_match_threshold: float
    title_year_bonus: float
    title_year_penalty: float
    title_sequel_penalty: float
    title_extra_words_penalty: float
    title_year_tolerance_movie: int
    title_year_tolerance_series: int
    max_results_per_plugin: int
    max_probe_count: int
    probe_concurrency: int
    resolve_target_count: int
    scoring_enabled: bool
    max_plugins_scored: int
    exploration_probability: float


class _StreamSorter(Protocol):
    """Sorts RankedStreams by language, quality, and hoster scores."""

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]: ...


# Where a request's search results come from: a fresh or stale cache entry,
# a new search, or a running one it joins
_Source = Literal["cache", "stale", "search", "joined"]

# Type aliases for injected pure functions.
_ConvertFn = Callable[..., list[RankedStream]]
_TitleFilterFn = Callable[..., list[SearchResult]]

log = structlog.get_logger(__name__)

# Cached answers resolve their other links in the background one title at a
# time: 17 cached titles asked within seconds started 17 runs at once, and 12
# Filemoon resolutions queued for the stealth browser's 2 pages until their
# 10 s timeout, which opened its breaker (dev-server end-to-end run, 2026-10-05)
_BACKGROUND_RUNS = 1

# The cached outcome of resolving a URL, without resolving it: (True, stream),
# (True, None) for a link cached as dead, (False, None) when not cached.
CachedResolutionCallback = Callable[[str], tuple[bool, ResolvedStream | None]]


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
        filter_fn: _TitleFilterFn,
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
        self._tmdb = tmdb
        self._plugins = plugins
        self._sorter = sorter
        self._convert_fn = convert_fn
        self._filter_fn = filter_fn
        self._user_agent = user_agent
        self._search_runner = PluginSearchRunner(
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
        )
        self._title_match_threshold = config.title_match_threshold
        self._title_year_bonus = config.title_year_bonus
        self._title_year_penalty = config.title_year_penalty
        self._title_sequel_penalty = config.title_sequel_penalty
        self._title_extra_words_penalty = config.title_extra_words_penalty
        self._title_year_tolerance_movie = config.title_year_tolerance_movie
        self._title_year_tolerance_series = config.title_year_tolerance_series
        self._stream_link_repo = stream_link_repo
        self._resolve_fn = resolve_fn
        self._cached_resolution_fn = cached_resolution_fn
        self._max_probe_count = config.max_probe_count
        self._probe_concurrency = config.probe_concurrency
        self._resolve_target = config.resolve_target_count
        self._deadline_s = config.stream_deadline_seconds
        self._plugin_timeout_s = config.plugin_timeout_seconds
        self._search_cache = SearchCache(cache, ttl_seconds=search_ttl_seconds)
        # Running searches per cache key (single-flight) and every task
        # that outlives a request (searches, background resolutions), for
        # aclose()
        self._searches: dict[str, SearchProgress] = {}
        # Resolutions of a cached answer's other links, one per cache key,
        # one cache key at a time
        self._background_resolutions: dict[str, asyncio.Task[Any]] = {}
        self._background_runs = asyncio.Semaphore(_BACKGROUND_RUNS)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._telemetry = telemetry
        self._score_store = score_store
        self._scoring_enabled = config.scoring_enabled
        self._max_plugins_scored = config.max_plugins_scored
        self._exploration_probability = config.exploration_probability
        self._pool = pool

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

        plugin_names = self._plugins.get_by_provides("stream")
        both_names = self._plugins.get_by_provides("both")
        all_names = sorted(set(plugin_names + both_names))

        if not all_names:
            log.warning("stremio_no_stream_plugins")
            stage.outcome = "no_plugins"
            return []

        with self._telemetry.stage("stremio_phase", phase="metadata") as metadata:
            # Scored plugin selection (when enabled and scores are available)
            selected = await self._select_plugins(all_names, category)

            # --- Multi-language title resolution ---
            all_langs = self._collect_languages(selected)
            title_infos = await self._resolve_title_infos(request, sorted(all_langs))

            primary_title_info = first_available_title(title_infos, sorted(all_langs))
            metadata.outcome = "not_found" if primary_title_info is None else "found"
        if primary_title_info is None:
            log.warning("stremio_title_not_found", imdb_id=request.imdb_id)
            stage.outcome = "no_title"
            return []

        # --- Per-language-group search + filter (cached per title) ---
        key = search_cache_key(request)
        progress, source = await self._search_progress(
            key,
            partial(
                self._search,
                key,
                self._group_by_languages(selected),
                title_infos,
                request,
                category,
                started=started,
                scored=len(selected) < len(all_names),
            ),
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
            ranked, resolved = await self._resolve(
                progress,
                plugin_languages,
                self._resolve_fn,
                deadline=deadline,
                key=key,
                from_cache=source in ("cache", "stale"),
            )
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

    def _collect_languages(self, plugin_names: list[str]) -> set[str]:
        """Collect all unique languages across the given plugins."""
        all_langs: set[str] = set()
        for name in plugin_names:
            all_langs.update(self._plugins.get_languages(name))
        return all_langs

    def _group_by_languages(
        self, plugin_names: list[str]
    ) -> dict[tuple[str, ...], list[str]]:
        """Group plugin names by their identical language lists."""
        groups: dict[tuple[str, ...], list[str]] = {}
        for name in plugin_names:
            key = tuple(self._plugins.get_languages(name))
            groups.setdefault(key, []).append(name)
        return groups

    async def _search_progress(
        self, key: str, search: Callable[[SearchProgress], Coroutine[Any, Any, None]]
    ) -> tuple[SearchProgress, _Source]:
        """The results for *key*: from the cache, or of the running or a new search.

        Requests for one key share one running search (single-flight) and
        read its results while it runs. A stale entry still answers while a
        background search refreshes it (stale-while-revalidate). The search
        runs as its own task, so a request that goes away does not cancel it
        for the others. Returns the progress and where it came from.
        """
        entry = await self._search_cache.get(key)
        if entry is None:
            return self._shared_search(key, search)
        stale = self._search_cache.is_stale(entry)
        if stale:
            self._shared_search(key, search)
        log.info(
            "stremio_search_cache_hit",
            cache_key=key,
            stale=stale,
            age_s=round(time.time() - entry.stored_at),
            result_count=len(entry.results),
        )
        return SearchProgress.finished(entry), "stale" if stale else "cache"

    def _shared_search(
        self, key: str, search: Callable[[SearchProgress], Coroutine[Any, Any, None]]
    ) -> tuple[SearchProgress, _Source]:
        """The running search for *key* (``joined``); starts *search* when
        none runs (``search``)."""
        progress = self._searches.get(key)
        if progress is not None:
            return progress, "joined"
        progress = SearchProgress()
        self._searches[key] = progress
        task = self._spawn(search(progress))
        task.add_done_callback(partial(self._search_done, key, progress))
        return progress, "search"

    def _search_done(
        self, key: str, progress: SearchProgress, _task: asyncio.Task[None]
    ) -> None:
        if self._searches.get(key) is progress:
            del self._searches[key]

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

    async def _search(
        self,
        key: str,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        progress: SearchProgress,
        *,
        started: float,
        scored: bool,
    ) -> None:
        """Search the plugins until they are done; store the matching results.

        The plugins get ``plugin_timeout_seconds`` from the request start.
        Their title-matching results go into *progress* as they arrive: the
        answers do not wait for the search (``_resolve``), they read it.
        """
        with self._telemetry.stage("stremio_phase", phase="search"):
            try:
                async with self._pool.request() as budget:
                    await self._search_lang_groups(
                        lang_groups,
                        title_infos,
                        request,
                        category,
                        progress,
                        scored=scored,
                        budget=budget,
                        # Plugins queue for slots; the search ends after the
                        # request started, not after each plugin's start
                        deadline=started + self._plugin_timeout_s,
                    )
            finally:
                progress.finish()
        await self._search_cache.put(key, progress.entry())

    async def _search_lang_groups(
        self,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        progress: SearchProgress,
        *,
        scored: bool,
        budget: ConcurrencyBudgetPort,
        deadline: float,
    ) -> None:
        """Search each language group; title-filter each plugin's results.

        Language groups are searched in parallel so that e.g. German and
        English plugins start at the same time instead of sequentially.
        Each plugin's results are filtered with its group's reference title
        when they arrive and go into *progress*.
        """

        async def _search_one_group(
            lang_key: tuple[str, ...],
            group_plugins: list[str],
        ) -> None:
            plugin_langs = list(lang_key)
            ref = build_multi_lang_reference(title_infos, plugin_langs)
            if ref is None:
                return

            queries = build_lang_group_queries(title_infos, plugin_langs)
            if not queries:
                return

            log.info(
                "stremio_search_start",
                imdb_id=request.imdb_id,
                title=ref.title,
                queries=queries,
                plugin_count=len(group_plugins),
                languages=plugin_langs,
                scored=scored,
            )

            async def _found(results: list[SearchResult]) -> None:
                progress.add(results, await self._title_filter(results, ref))

            await self._search_runner.search_with_fallback(
                group_plugins,
                queries,
                category,
                season=request.season,
                episode=request.episode,
                budget=budget,
                deadline=deadline,
                on_results=_found,
            )

        await asyncio.gather(
            *(
                _search_one_group(lang_key, group_plugins)
                for lang_key, group_plugins in lang_groups.items()
            )
        )

    async def _title_filter(
        self, results: list[SearchResult], ref: TitleMatchInfo
    ) -> list[SearchResult]:
        """The results whose title matches *ref* (in the executor: CPU work)."""
        if not results:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._filter_fn(
                results,
                ref,
                self._title_match_threshold,
                year_bonus=self._title_year_bonus,
                year_penalty=self._title_year_penalty,
                sequel_penalty=self._title_sequel_penalty,
                extra_words_penalty=self._title_extra_words_penalty,
                year_tolerance_movie=self._title_year_tolerance_movie,
                year_tolerance_series=self._title_year_tolerance_series,
            ),
        )

    async def _cache_and_proxy(
        self,
        streams: list[StremioStream],
        ranked: list[RankedStream],
        resolved_map: dict[int, ResolvedStream],
        base_url: str,
    ) -> list[StremioStream]:
        """Point the streams at their playable URLs.

        With a resolve callback, the streams in *resolved_map* (by index)
        get their direct video URL or an HLS proxy URL, with
        ``behaviorHints.proxyHeaders`` so Stremio sends the right HTTP
        headers (Referer, User-Agent); the others are dropped. Without one,
        streams go through the ``/play/`` endpoint. Only streams served
        through our own endpoints (``/play/``, the HLS proxy) get their link
        saved; one save per ranked stream (dozens) delayed the answer by
        seconds.
        """
        answer: list[tuple[StremioStream, CachedStreamLink | None]] = []
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
            served_here = built.url.startswith(base_url)
            link = build_cache_link(sid, ranked[i], resolved) if served_here else None
            answer.append((built, link))

        # --- Cache step (parallel writes) for streams served by us ---
        unsaved = await self._save_links([lnk for _, lnk in answer if lnk is not None])
        proxied = [
            s for s, lnk in answer if lnk is None or lnk.stream_id not in unsaved
        ]
        skipped_unsaved = len(answer) - len(proxied)
        if skipped_echo or skipped_unresolved or skipped_unsaved:
            log.info(
                "stremio_streams_skipped",
                skipped_echo=skipped_echo,
                skipped_unresolved=skipped_unresolved,
                skipped_unsaved=skipped_unsaved,
            )
        return proxied

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

    async def _resolve(
        self,
        progress: SearchProgress,
        plugin_languages: dict[str, str],
        resolve_fn: ResolveCallback,
        *,
        deadline: float,
        key: str,
        from_cache: bool,
    ) -> tuple[list[RankedStream], dict[int, ResolvedStream]]:
        """The ranked streams and their resolutions, by index.

        Results from the search cache go out at once with the resolutions in
        the resolver's cache when one of them is a video. The links without
        one resolve in the background for the next request, one run per
        cache key at a time (such answers waited for the resolve grace,
        4.1-4.4 s, for one link resolved for the first time). Otherwise the
        streams resolve as ``_resolve_as_results_arrive`` describes.
        """
        if from_cache and self._cached_resolution_fn is not None:
            ranked = await self._rank(progress.results, plugin_languages)
            ranked = ranked[: self._max_probe_count]
            cached = self._cached_resolutions(ranked, self._cached_resolution_fn)
            if any(is_direct_video_url(r, ranked[i].url) for i, r in cached.items()):
                log.info("stremio_resolve_from_cache", resolved=len(cached))
                self._telemetry.count("stremio_phase", "cached", phase="resolve")
                if key not in self._background_resolutions:
                    task = self._spawn(
                        self._resolve_in_background(
                            progress, plugin_languages, resolve_fn
                        )
                    )
                    self._background_resolutions[key] = task
                    task.add_done_callback(
                        lambda _: self._background_resolutions.pop(key, None)
                    )
                return ranked, cached
        return await self._resolve_as_results_arrive(
            progress, plugin_languages, resolve_fn, deadline=deadline
        )

    async def _resolve_in_background(
        self,
        progress: SearchProgress,
        plugin_languages: dict[str, str],
        resolve_fn: ResolveCallback,
    ) -> None:
        """Resolve a cached answer's other links for the next request.

        One title at a time (``_BACKGROUND_RUNS``); the deadline counts
        from the run's start, so a title that waited keeps its whole time.
        """
        async with self._background_runs:
            await self._resolve_as_results_arrive(
                progress,
                plugin_languages,
                resolve_fn,
                deadline=time.monotonic() + self._deadline_s,
                background=True,
            )

    @staticmethod
    def _cached_resolutions(
        ranked: list[RankedStream], cached_fn: CachedResolutionCallback
    ) -> dict[int, ResolvedStream]:
        """Each hoster's best stream whose resolution is cached, by index.

        A hoster's streams are taken in rank order past the links cached as
        dead and the links not resolved yet: a plugin that answered after
        the first answer can rank a new link of a hoster first, and the
        hoster's stream of that answer dropped out (the background
        resolution resolves the new link for the next request).
        """
        hosters: dict[object, list[int]] = {}
        for i, stream in enumerate(ranked):
            hosters.setdefault(hoster_key(stream) or stream.url, []).append(i)
        found: dict[int, ResolvedStream] = {}
        for indices in hosters.values():
            for i in indices:
                _, resolved = cached_fn(ranked[i].url)
                if resolved is not None:
                    found[i] = resolved
                    break
        return found

    async def _resolve_as_results_arrive(
        self,
        progress: SearchProgress,
        plugin_languages: dict[str, str],
        resolve_fn: ResolveCallback,
        *,
        deadline: float,
        background: bool = False,
    ) -> tuple[list[RankedStream], dict[int, ResolvedStream]]:
        """Resolve the search's links while it runs; stop when the answer is due.

        Each new batch of results is ranked with the ones before and handed
        to a ``HosterResolution`` (one link per hoster at a time, rank
        order). The answer is due when ``resolve_target_count`` hosters have
        a video, or when the search is done and no resolution runs or is
        due, at the latest at *deadline* (``time.monotonic()``); resolutions
        still running then are cancelled. In the *background* (no answer
        waits) there is no target: every hoster resolves until done or the
        deadline, which fills the resolver's cache.
        """
        changed = asyncio.Event()
        resolution = HosterResolution(
            resolve_fn,
            concurrency=self._probe_concurrency,
            limit=self._max_probe_count,
            changed=changed,
        )
        target = 0 if background else self._resolve_target
        ranked: list[RankedStream] = []
        seen = 0
        reason = "deadline"
        phase = "background_resolve" if background else "resolve"
        progress.listen(changed)
        with self._telemetry.stage("stremio_phase", phase=phase) as stage:
            try:
                while True:
                    changed.clear()
                    if seen < len(progress.results):
                        new = progress.results[seen:]
                        seen += len(new)
                        converted = await self._convert(new, plugin_languages)
                        # Score only the new streams: re-sorting all of them
                        # per batch scored every stream again (10 ms per
                        # request on x86 for 200). The stable merge keeps the
                        # full sort's order
                        ranked = list(
                            heapq.merge(
                                ranked,
                                self._sorter.sort(converted),
                                key=lambda s: -s.rank_score,
                            )
                        )
                        resolution.update(ranked)
                        continue
                    if target > 0 and resolution.videos() >= target:
                        reason = "target"
                        break
                    if progress.done and not resolution.pending():
                        reason = "done"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(remaining):
                            await changed.wait()
            finally:
                progress.unlisten(changed)
                unfinished = resolution.unfinished
                await resolution.aclose()
            stage.outcome = reason
        resolved = resolution.resolved()
        log.info(
            "stremio_resolve_complete",
            background=background,
            reason=reason,
            search_done=progress.done,
            total=len(resolution.ranked),
            attempted=resolution.started - unfinished,
            resolved=len(resolved),
            video_streams=resolution.videos(),
            unfinished=unfinished,
        )
        return resolution.ranked, resolved

    async def _rank(
        self, results: list[SearchResult], plugin_languages: dict[str, str]
    ) -> list[RankedStream]:
        """The streams of *results*, best first."""
        return self._sorter.sort(await self._convert(results, plugin_languages))

    async def _convert(
        self, results: list[SearchResult], plugin_languages: dict[str, str]
    ) -> list[RankedStream]:
        """The streams of *results* (in the executor: CPU work)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._convert_fn(results, plugin_languages=plugin_languages),
        )

    async def _resolve_title_info(
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

    async def _resolve_title_infos(
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
            *(self._resolve_title_info(request, language=lang) for lang in languages)
        )
        return dict(zip(languages, infos))

    async def _select_plugins(
        self,
        all_names: list[str],
        category: int,
    ) -> list[str]:
        """Select plugins to search, using scores when available.

        When scoring is disabled or no scores exist yet, returns all
        plugins (graceful cold-start fallback).

        When scoring is active, selects the top-N plugins by
        ``final_score`` and optionally adds one random exploration slot.
        """
        if not self._scoring_enabled or self._score_store is None:
            return all_names

        # Collect scores for each plugin (using "current" bucket as proxy)
        snapshots = await asyncio.gather(
            *(
                self._score_store.get_snapshot(name, category, "current")
                for name in all_names
            )
        )
        scored: list[tuple[str, float, float]] = [
            (name, snap.final_score, snap.confidence)
            if snap is not None
            else (name, 0.5, 0.0)
            for name, snap in zip(all_names, snapshots)
        ]

        # Cold-start guard: need at least 50% of plugins with confidence > 0.1
        confident_count = sum(1 for _, _, c in scored if c > 0.1)
        if confident_count < len(all_names) * 0.5:
            log.debug(
                "scored_selection_cold_start",
                confident=confident_count,
                total=len(all_names),
            )
            return all_names

        # Sort by final_score descending, pick top-N
        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = scored[: self._max_plugins_scored]
        selected_names = [name for name, _, _ in top_n]

        # Exploration slot: with probability, add one mid-score plugin
        remaining = [
            (name, score, conf)
            for name, score, conf in scored[self._max_plugins_scored :]
            if conf >= 0.1
        ]
        if remaining and random.random() < self._exploration_probability:
            explorer = random.choice(remaining)
            selected_names.append(explorer[0])

        log.info(
            "scored_plugin_selection",
            top_n=[f"{n}:{s:.2f}" for n, s, _ in top_n],
            exploration=len(selected_names) > self._max_plugins_scored,
            total_available=len(all_names),
        )
        return selected_names
