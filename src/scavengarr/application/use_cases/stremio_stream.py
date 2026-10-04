"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Protocol
from uuid import uuid4

import structlog

from scavengarr.application.stremio.plugin_search import (
    BrowserWarmupFn,
    CircuitBreaker,
    EpisodeFilterFn,
    LateSearch,
    PluginSearchRunner,
    finish_late,
)
from scavengarr.application.stremio.queries import (
    build_lang_group_queries,
    build_multi_lang_reference,
    first_available_title,
)
from scavengarr.application.stremio.search_cache import (
    CachedSearch,
    SearchCache,
    search_cache_key,
)
from scavengarr.application.stremio.stream_builder import (
    build_cache_link,
    build_stream_from_resolved,
    deduplicate_by_hoster,
    format_stream,
    hoster_key,
    is_direct_video_url,
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
from scavengarr.domain.ports.tmdb import TmdbClientPort

# ---------------------------------------------------------------------------
# Protocols — define what this use case needs from its dependencies.
# Infrastructure components satisfy these via structural subtyping.
# ---------------------------------------------------------------------------


class _StremioConfig(Protocol):
    """Configuration values consumed by StremioStreamUseCase."""

    plugin_timeout_seconds: float
    search_soft_deadline_seconds: float
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
    resolve_grace_seconds: float
    scoring_enabled: bool
    max_plugins_scored: int
    exploration_probability: float


class _StreamSorter(Protocol):
    """Sorts RankedStreams by language, quality, and hoster scores."""

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]: ...


class _MetricsRecorder(Protocol):
    """Records plugin search metrics."""

    def record_plugin_search(
        self,
        name: str,
        duration_ns: int,
        result_count: int,
        *,
        success: bool,
    ) -> None: ...


# Type aliases for injected pure functions.
_ConvertFn = Callable[..., list[RankedStream]]
_TitleFilterFn = Callable[..., list[SearchResult]]

log = structlog.get_logger(__name__)

# Resolution always gets this long after the plugin search, even when the
# search alone used up the stream deadline (otherwise nothing is returned).
_MIN_RESOLVE_WINDOW_S = 2.0


class _HosterQueues:
    """Stream indices grouped by hoster and language, handed out in rank order.

    A group gets its next stream only after the previous one failed.
    Streams without a hoster name each form their own group.
    """

    def __init__(self, ranked: list[RankedStream]) -> None:
        self._queues: dict[tuple[str, str] | int, deque[int]] = {}
        for i, stream in enumerate(ranked):
            self._queues.setdefault(hoster_key(stream) or i, deque()).append(i)
        self._key_of = {i: key for key, q in self._queues.items() for i in q}

    def first(self) -> list[int]:
        """The best stream of every hoster."""
        return [q.popleft() for q in self._queues.values()]

    def next_after(self, failed: list[int]) -> list[int]:
        """The next stream of each hoster whose stream in *failed* failed."""
        queues = (self._queues[self._key_of[i]] for i in failed)
        return [q.popleft() for q in queues if q]


@dataclass(frozen=True)
class _LateGroup:
    """Late plugin searches of one language group, with its reference title."""

    ref: TitleMatchInfo
    searches: list[LateSearch]

    def running(self) -> _LateGroup:
        """The searches that have not finished yet."""
        return replace(self, searches=[s for s in self.searches if not s.task.done()])


# Callback type for resolving hoster embed URLs to playable video URLs.
# Accepts (url, hoster_hint), returns ResolvedStream or None.
ResolveCallback = Callable[[str, str], Awaitable[ResolvedStream | None]]


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
        config: _StremioConfig,
        sorter: _StreamSorter,
        convert_fn: _ConvertFn,
        filter_fn: _TitleFilterFn,
        episode_filter_fn: EpisodeFilterFn,
        user_agent: str,
        max_results_var: ContextVar[int | None],
        stream_link_repo: StreamLinkRepository | None = None,
        resolve_fn: ResolveCallback | None = None,
        metrics: _MetricsRecorder | None = None,
        score_store: PluginScoreStorePort | None = None,
        browser_warmup_fn: BrowserWarmupFn | None = None,
        pool: ConcurrencyPoolPort,
        circuit_breaker: CircuitBreaker | None = None,
        mirror_groups: Mapping[str, str] | None = None,
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
            metrics=metrics,
            circuit_breaker=circuit_breaker,
            browser_warmup_fn=browser_warmup_fn,
            mirror_groups=mirror_groups,
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
        self._max_probe_count = config.max_probe_count
        self._probe_concurrency = config.probe_concurrency
        self._resolve_target = config.resolve_target_count
        self._resolve_grace_s = config.resolve_grace_seconds
        self._deadline_s = config.stream_deadline_seconds
        self._plugin_timeout_s = config.plugin_timeout_seconds
        self._soft_deadline_s = config.search_soft_deadline_seconds
        self._search_cache = SearchCache(cache, ttl_seconds=search_ttl_seconds)
        # Running searches per cache key (single-flight) and every task
        # that outlives a request (refreshes, late plugins), for aclose()
        self._searches: dict[str, asyncio.Task[CachedSearch]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._metrics = metrics
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
        started = time.monotonic()
        category = 2000 if request.content_type == "movie" else 5000

        plugin_names = self._plugins.get_by_provides("stream")
        both_names = self._plugins.get_by_provides("both")
        all_names = sorted(set(plugin_names + both_names))

        if not all_names:
            log.warning("stremio_no_stream_plugins")
            return []

        # Scored plugin selection (when enabled and scores are available)
        selected = await self._select_plugins(all_names, category)

        # --- Multi-language title resolution ---
        all_langs = self._collect_languages(selected)
        title_infos = await self._resolve_title_infos(request, sorted(all_langs))

        primary_title_info = first_available_title(title_infos, sorted(all_langs))
        if primary_title_info is None:
            log.warning("stremio_title_not_found", imdb_id=request.imdb_id)
            return []

        # --- Per-language-group search + filter (cached per title) ---
        key = search_cache_key(request)
        searched = await self._cached_search(
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
        filtered = searched.results

        if not filtered:
            if searched.total:
                log.info(
                    "stremio_all_filtered",
                    imdb_id=request.imdb_id,
                    total=searched.total,
                )
            else:
                log.info(
                    "stremio_search_no_results",
                    imdb_id=request.imdb_id,
                )
            return []

        # Cached results can come from plugins this request did not select
        plugin_languages: dict[str, str] = {
            name: langs[0]
            for name in all_names
            if (langs := self._plugins.get_languages(name))
        }

        loop = asyncio.get_running_loop()
        ranked = await loop.run_in_executor(
            None,
            lambda: self._convert_fn(filtered, plugin_languages=plugin_languages),
        )
        sorted_streams = self._sorter.sort(ranked)
        if self._resolve_fn is None:
            sorted_streams = deduplicate_by_hoster(sorted_streams)
        else:
            # Deduplicated by the resolution (one resolved stream per
            # hoster): a hoster keeps its best stream that actually resolves
            sorted_streams = sorted_streams[: self._max_probe_count]

        streams = [
            format_stream(
                s,
                reference_title=primary_title_info.title,
                year=primary_title_info.year,
                season=request.season,
                episode=request.episode,
            )
            for s in sorted_streams
        ]

        if self._stream_link_repo and base_url:
            deadline = max(
                started + self._deadline_s, time.monotonic() + _MIN_RESOLVE_WINDOW_S
            )
            streams = await self._cache_and_proxy(
                streams, sorted_streams, base_url, deadline=deadline
            )

        log.info(
            "stremio_search_complete",
            imdb_id=request.imdb_id,
            result_count=searched.total,
            filtered_count=len(filtered),
            stream_count=len(streams),
        )

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

    async def _cached_search(
        self, key: str, search: Callable[[], Coroutine[Any, Any, CachedSearch]]
    ) -> CachedSearch:
        """The search results for *key*: cached, from a running search, or new.

        Requests for one key share one running search (single-flight). A
        stale entry still answers while a background search refreshes it
        (stale-while-revalidate). The search runs as its own task, so a
        request that goes away does not cancel it for the others.
        """
        entry = await self._search_cache.get(key)
        if entry is None:
            return await asyncio.shield(self._shared_search(key, search))
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
        return entry

    def _shared_search(
        self, key: str, search: Callable[[], Coroutine[Any, Any, CachedSearch]]
    ) -> asyncio.Task[CachedSearch]:
        """The running search for *key*; starts *search* when none runs."""
        task = self._searches.get(key)
        if task is None:
            task = self._spawn(search())
            self._searches[key] = task
            task.add_done_callback(partial(self._search_done, key))
        return task

    def _search_done(self, key: str, task: asyncio.Task[CachedSearch]) -> None:
        if self._searches.get(key) is task:
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
        """Cancel the searches still running (refreshes, late plugins)."""
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
        *,
        started: float,
        scored: bool,
    ) -> CachedSearch:
        """Search the plugins, store the title-matching results, answer.

        With the cache on, the answer goes out at the soft deadline when
        there are results. Plugins still running then go on as late
        plugins, and their results are added to the cache entry for the
        next request. Without results the answer waits for the late
        plugins until the hard deadline (``plugin_timeout_seconds``), as
        without the early answer. With the cache off, plugins are cut at
        the hard deadline.
        """
        cached = self._search_cache.enabled
        hard = started + self._plugin_timeout_s
        soft = min(started + self._soft_deadline_s, hard) if cached else hard
        late: list[_LateGroup] = []
        try:
            async with self._pool.request() as budget:
                all_results, filtered = await self._search_lang_groups(
                    lang_groups,
                    title_infos,
                    request,
                    category,
                    scored=scored,
                    budget=budget,
                    # Plugins queue for slots; the search ends after the
                    # request started, not after each plugin's start
                    deadline=soft,
                    late=late if cached else None,
                )
            entry = CachedSearch(
                results=filtered, total=len(all_results), stored_at=time.time()
            )
            searches = [s.task for group in late for s in group.searches]
            if searches and not filtered:
                await asyncio.wait(searches, timeout=max(hard - time.monotonic(), 0.0))
                entry = entry.merged(*await self._late_results(late))
                late = [group.running() for group in late]
            await self._search_cache.put(key, entry)
        except BaseException:
            # Cancelled (shutdown): late plugins must not run on unowned
            await finish_late(
                [s for group in late for s in group.searches], timeout=0.0
            )
            raise
        late = [group for group in late if group.searches]
        if late:
            self._spawn(self._complete_late(key, late))
        return entry

    async def _complete_late(self, key: str, late: list[_LateGroup]) -> None:
        """Wait for the late plugins; add their results to the cache entry.

        They get ``plugin_timeout_seconds`` more and are cancelled then.
        """
        searches = [s for group in late for s in group.searches]
        await finish_late(searches, timeout=self._plugin_timeout_s)
        results, total = await self._late_results(late)
        entry = await self._search_cache.get(key) or CachedSearch(
            results=[], total=0, stored_at=time.time()
        )
        merged = entry.merged(results, total)
        added = len(merged.results) - len(entry.results)
        if added:
            await self._search_cache.put(key, merged)
        log.info(
            "stremio_late_plugins_done",
            cache_key=key,
            plugins=sorted({s.plugin for s in searches}),
            cancelled=sum(s.task.cancelled() for s in searches),
            result_count=total,
            added=added,
        )

    async def _late_results(
        self, late: list[_LateGroup]
    ) -> tuple[list[SearchResult], int]:
        """Title-matching results of the finished late searches.

        Returns them with the number of results before the title filter.
        """
        found = [[r for s in group.searches for r in s.results] for group in late]
        matching = await asyncio.gather(
            *(
                self._title_filter(results, group.ref)
                for results, group in zip(found, late, strict=True)
            )
        )
        return [r for group in matching for r in group], sum(map(len, found))

    async def _search_lang_groups(
        self,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        *,
        scored: bool,
        budget: ConcurrencyBudgetPort,
        deadline: float,
        late: list[_LateGroup] | None = None,
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Search and filter each language group, returning aggregated results.

        Language groups are searched in parallel so that e.g. German and
        English plugins start at the same time instead of sequentially.
        With a *late* collector, plugins the deadline cuts run on and are
        added to it per language group instead of being cancelled.

        Returns (all_results, filtered_results).
        """

        async def _search_one_group(
            lang_key: tuple[str, ...],
            group_plugins: list[str],
        ) -> tuple[list[SearchResult], list[SearchResult]]:
            plugin_langs = list(lang_key)
            ref = build_multi_lang_reference(title_infos, plugin_langs)
            if ref is None:
                return [], []

            queries = build_lang_group_queries(title_infos, plugin_langs)
            if not queries:
                return [], []

            log.info(
                "stremio_search_start",
                imdb_id=request.imdb_id,
                title=ref.title,
                queries=queries,
                plugin_count=len(group_plugins),
                languages=plugin_langs,
                scored=scored,
            )

            group_late: list[LateSearch] | None = None
            if late is not None:
                group_late = []
                late.append(_LateGroup(ref, group_late))
            group_results = await self._search_runner.search_with_fallback(
                group_plugins,
                queries,
                category,
                season=request.season,
                episode=request.episode,
                budget=budget,
                deadline=deadline,
                late=group_late,
            )
            return group_results, await self._title_filter(group_results, ref)

        group_tasks = [
            _search_one_group(lang_key, group_plugins)
            for lang_key, group_plugins in lang_groups.items()
        ]
        group_outcomes = await asyncio.gather(*group_tasks)

        all_results: list[SearchResult] = []
        filtered: list[SearchResult] = []
        for group_all, group_filt in group_outcomes:
            all_results.extend(group_all)
            filtered.extend(group_filt)

        return all_results, filtered

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
        base_url: str,
        *,
        deadline: float,
    ) -> list[StremioStream]:
        """Resolve the streams and point them at their playable URLs.

        When a resolve callback is configured, resolves hoster embed URLs
        to direct video URLs and attaches ``behaviorHints.proxyHeaders``
        so Stremio sends the correct HTTP headers (Referer, User-Agent)
        when playing the stream. Without one, streams go through the
        ``/play/`` endpoint. Only streams served through our own endpoints
        (``/play/``, the HLS proxy) get their link saved; one save per
        ranked stream (dozens) delayed the answer by seconds.
        """
        # --- Resolve step: extract direct video URLs + headers ---
        resolved_map: dict[int, ResolvedStream] = {}
        if self._resolve_fn is not None:
            resolved_map = await self._resolve_top_streams(
                ranked, deadline, self._resolve_fn
            )

        answer: list[tuple[StremioStream, CachedStreamLink | None]] = []
        skipped_echo = 0
        skipped_unresolved = 0
        has_resolver = bool(self._resolve_fn)
        for i, stream in enumerate(streams):
            sid = uuid4().hex
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

    async def _resolve_top_streams(
        self,
        ranked: list[RankedStream],
        deadline: float,
        resolve_fn: ResolveCallback,
    ) -> dict[int, ResolvedStream]:
        """Resolve the top streams to direct video URLs, one per hoster.

        Hosters are resolved in parallel, the streams of one hoster in rank
        order: a hoster's next stream is only tried after its better one
        failed, and none after one resolved.  This yields the best working
        stream per hoster without opening connections for every candidate
        at once (bursts to dozens of CDNs look like a port scan to home
        routers, which then block the machine).  Streams without a hoster
        name are each their own group.

        Uses early-stop: once ``resolve_target_count`` genuine video URLs
        have been extracted, remaining tasks are cancelled.  At *deadline*
        (``time.monotonic()`` value) unfinished resolutions are cancelled
        and what is resolved so far is returned; once the first video URL
        is there, that happens ``resolve_grace_seconds`` later at the
        latest (browser-resolved hosters take 3-7 s and held answers that
        were complete but for them until the deadline).

        Returns a mapping of stream index -> ResolvedStream for
        successfully resolved streams.  Failed resolutions are omitted.
        """
        limit = min(len(ranked), self._max_probe_count)
        semaphore = asyncio.Semaphore(self._probe_concurrency)
        hosters = _HosterQueues(ranked[:limit])

        async def _resolve_one(idx: int) -> tuple[int, ResolvedStream | None]:
            async with semaphore:
                r = ranked[idx]
                try:
                    return idx, await resolve_fn(r.url, r.hoster)
                except Exception:
                    log.debug(
                        "stremio_resolve_failed",
                        index=idx,
                        hoster=r.hoster,
                        url=r.url[:80],
                        exc_info=True,
                    )
                    return idx, None

        pending = {asyncio.create_task(_resolve_one(i)) for i in hosters.first()}
        resolved_map: dict[int, ResolvedStream] = {}
        video_count = 0
        target = self._resolve_target
        attempted = 0
        timed_out = False
        end = deadline

        while pending:
            remaining = end - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                timed_out = True
                break
            attempted += len(done)
            first_video = video_count == 0
            video_count += self._collect_resolved(done, ranked, resolved_map)
            if first_video and video_count and self._resolve_grace_s > 0:
                end = min(end, time.monotonic() + self._resolve_grace_s)
            failed = [idx for idx, res in (t.result() for t in done) if res is None]
            pending |= {
                asyncio.create_task(_resolve_one(i)) for i in hosters.next_after(failed)
            }

            if target > 0 and video_count >= target:
                break

        # Cancel remaining tasks once target reached
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        log.info(
            "stremio_resolve_complete",
            total=limit,
            attempted=attempted,
            resolved=len(resolved_map),
            video_streams=video_count,
            early_stop=target > 0 and video_count >= target,
            deadline_hit=timed_out and end == deadline,
            grace_hit=timed_out and end < deadline,
            unfinished=len(pending) if timed_out else 0,
        )
        return resolved_map

    @staticmethod
    def _collect_resolved(
        done: set[asyncio.Task[tuple[int, ResolvedStream | None]]],
        ranked: list[RankedStream],
        resolved_map: dict[int, ResolvedStream],
    ) -> int:
        """Store finished resolutions; return how many are direct videos."""
        videos = 0
        for task in done:
            idx, resolved = task.result()
            if resolved is None:
                continue
            resolved_map[idx] = resolved
            original_url = ranked[idx].url if idx < len(ranked) else ""
            if is_direct_video_url(resolved, original_url):
                videos += 1
        return videos

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
