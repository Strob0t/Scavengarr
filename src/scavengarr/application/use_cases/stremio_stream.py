"""Stremio stream resolution use case.

IMDb ID -> TMDB title -> parallel plugin search
-> convert -> sort -> StremioStream list.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

import structlog

from scavengarr.application.stremio.plugin_search import (
    BrowserWarmupFn,
    CircuitBreaker,
    EpisodeFilterFn,
    PluginSearchRunner,
)
from scavengarr.application.stremio.queries import (
    build_lang_group_queries,
    build_multi_lang_reference,
    first_available_title,
)
from scavengarr.application.stremio.stream_builder import (
    build_cache_links,
    build_stream_from_resolved,
    deduplicate_by_hoster,
    format_stream,
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
    stream_deadline_seconds: float
    title_match_threshold: float
    title_year_bonus: float
    title_year_penalty: float
    title_sequel_penalty: float
    title_year_tolerance_movie: int
    title_year_tolerance_series: int
    max_results_per_plugin: int
    probe_at_stream_time: bool
    max_probe_count: int
    probe_concurrency: int
    resolve_target_count: int
    scoring_enabled: bool
    max_plugins_scored: int
    exploration_probability: float


class _StreamSorter(Protocol):
    """Sorts RankedStreams by language, quality, and hoster scores."""

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]: ...


class _MetricsRecorder(Protocol):
    """Records search and probe metrics."""

    def record_plugin_search(
        self,
        name: str,
        duration_ns: int,
        result_count: int,
        *,
        success: bool,
    ) -> None: ...

    def record_probe(
        self,
        total: int,
        alive: int,
        dead: int,
        cf_blocked: int,
        duration_ns: int,
    ) -> None: ...


# Type aliases for injected pure functions.
_ConvertFn = Callable[..., list[RankedStream]]
_TitleFilterFn = Callable[..., list[SearchResult]]

log = structlog.get_logger(__name__)

# Resolution always gets this long after the plugin search, even when the
# search alone used up the stream deadline (otherwise nothing is returned).
_MIN_RESOLVE_WINDOW_S = 2.0


class _HosterQueues:
    """Stream indices grouped by hoster, handed out in rank order.

    A hoster gets its next stream only after the previous one failed.
    Streams without a hoster name each form their own group.
    """

    def __init__(self, ranked: list[RankedStream]) -> None:
        self._queues: dict[str, deque[int]] = {}
        for i, stream in enumerate(ranked):
            self._queues.setdefault(stream.hoster or f"#{i}", deque()).append(i)
        self._key_of = {i: key for key, q in self._queues.items() for i in q}

    def first(self) -> list[int]:
        """The best stream of every hoster."""
        return [q.popleft() for q in self._queues.values()]

    def next_after(self, failed: list[int]) -> list[int]:
        """The next stream of each hoster whose stream in *failed* failed."""
        queues = (self._queues[self._key_of[i]] for i in failed)
        return [q.popleft() for q in queues if q]


# Callback type for probing hoster URLs at /stream time.
# Accepts list of (index, url) tuples, returns set of alive indices.
ProbeCallback = Callable[[list[tuple[int, str]]], Awaitable[set[int]]]

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
        probe_fn: ProbeCallback | None = None,
        resolve_fn: ResolveCallback | None = None,
        metrics: _MetricsRecorder | None = None,
        score_store: PluginScoreStorePort | None = None,
        browser_warmup_fn: BrowserWarmupFn | None = None,
        pool: ConcurrencyPoolPort,
        circuit_breaker: CircuitBreaker | None = None,
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
        )
        self._title_match_threshold = config.title_match_threshold
        self._title_year_bonus = config.title_year_bonus
        self._title_year_penalty = config.title_year_penalty
        self._title_sequel_penalty = config.title_sequel_penalty
        self._title_year_tolerance_movie = config.title_year_tolerance_movie
        self._title_year_tolerance_series = config.title_year_tolerance_series
        self._stream_link_repo = stream_link_repo
        self._probe_fn = probe_fn
        self._resolve_fn = resolve_fn
        self._probe_at_stream_time = config.probe_at_stream_time
        self._max_probe_count = config.max_probe_count
        self._probe_concurrency = config.probe_concurrency
        self._resolve_target = config.resolve_target_count
        self._deadline_s = config.stream_deadline_seconds
        self._plugin_timeout_s = config.plugin_timeout_seconds
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

        # --- Per-language-group search + filter ---
        lang_groups = self._group_by_languages(selected)

        async with self._pool.request() as budget:
            all_results, filtered = await self._search_lang_groups(
                lang_groups,
                title_infos,
                request,
                category,
                all_names_count=len(all_names),
                selected_count=len(selected),
                budget=budget,
                # Plugins queue for slots; the search ends plugin_timeout
                # after the request started, not after each plugin's start
                deadline=started + self._plugin_timeout_s,
            )

        if not filtered:
            if all_results:
                log.info(
                    "stremio_all_filtered",
                    imdb_id=request.imdb_id,
                    total=len(all_results),
                )
            else:
                log.info(
                    "stremio_search_no_results",
                    imdb_id=request.imdb_id,
                )
            return []

        plugin_languages: dict[str, str] = {
            name: self._plugins.get_languages(name)[0]
            for name in selected
            if self._plugins.get_languages(name)
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
            result_count=len(all_results),
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

    async def _search_lang_groups(
        self,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        *,
        all_names_count: int,
        selected_count: int,
        budget: ConcurrencyBudgetPort,
        deadline: float,
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Search and filter each language group, returning aggregated results.

        Language groups are searched in parallel so that e.g. German and
        English plugins start at the same time instead of sequentially.

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
                scored=selected_count < all_names_count,
            )

            group_results = await self._search_runner.search_with_fallback(
                group_plugins,
                queries,
                category,
                season=request.season,
                episode=request.episode,
                budget=budget,
                deadline=deadline,
            )

            if not group_results:
                return group_results, []

            loop = asyncio.get_running_loop()
            group_filtered = await loop.run_in_executor(
                None,
                lambda ref=ref, gr=group_results: self._filter_fn(
                    gr,
                    ref,
                    self._title_match_threshold,
                    year_bonus=self._title_year_bonus,
                    year_penalty=self._title_year_penalty,
                    sequel_penalty=self._title_sequel_penalty,
                    year_tolerance_movie=self._title_year_tolerance_movie,
                    year_tolerance_series=self._title_year_tolerance_series,
                ),
            )
            return group_results, group_filtered

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

    async def _cache_and_proxy(
        self,
        streams: list[StremioStream],
        ranked: list[RankedStream],
        base_url: str,
        *,
        deadline: float,
    ) -> list[StremioStream]:
        """Cache hoster URLs and replace stream URLs with proxy play links.

        When a probe callback is configured and enabled, performs a
        lightweight GET probe on each hoster embed URL to filter dead
        links before caching. Only the top ``max_probe_count`` streams
        are probed; the rest pass through unchecked.

        When a resolve callback is configured, resolves hoster embed URLs
        to direct video URLs and attaches ``behaviorHints.proxyHeaders``
        so Stremio sends the correct HTTP headers (Referer, User-Agent)
        when playing the stream.  Streams that fail to resolve fall back
        to the ``/play/`` proxy endpoint.
        """
        # --- Probe step: filter dead links ---
        # Skip probing when a resolve callback is configured because
        # resolution implicitly checks liveness (failed → skipped).
        if self._probe_fn and self._probe_at_stream_time and not self._resolve_fn:
            streams, ranked = await self._probe_streams(streams, ranked)

        # --- Resolve step: extract direct video URLs + headers ---
        resolved_map: dict[int, ResolvedStream] = {}
        if self._resolve_fn:
            resolved_map = await self._resolve_top_streams(ranked, deadline)

        # --- Cache step (parallel writes) ---
        stream_ids = [uuid4().hex for _ in streams]
        links = build_cache_links(stream_ids, ranked, resolved_map)
        unsaved = await self._save_links(links)

        proxied: list[StremioStream] = []
        skipped_echo = 0
        skipped_unresolved = 0
        # Streams through our own /play/ or HLS proxy need their saved link
        skipped_unsaved = 0
        has_resolver = bool(self._resolve_fn)
        for i, (stream, sid) in enumerate(zip(streams, stream_ids)):
            resolved = resolved_map.get(i)
            if resolved is not None:
                original_url = ranked[i].url if i < len(ranked) else ""
                built = build_stream_from_resolved(
                    stream, resolved, original_url, sid, base_url, self._user_agent
                )
                if built is None:
                    skipped_echo += 1
                elif sid in unsaved and built.url.startswith(base_url):
                    skipped_unsaved += 1
                else:
                    proxied.append(built)
            elif has_resolver:
                # Resolver is configured but returned None — skip this stream.
                # The /play/ proxy would also fail (502).
                skipped_unresolved += 1
            elif sid in unsaved:
                skipped_unsaved += 1
            else:
                # No resolver configured — proxy through /play/ endpoint
                proxy_url = f"{base_url}/api/v1/stremio/play/{sid}"
                proxied.append(
                    StremioStream(
                        name=stream.name,
                        description=stream.description,
                        url=proxy_url,
                    )
                )
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

    async def _probe_streams(
        self,
        streams: list[StremioStream],
        ranked: list[RankedStream],
    ) -> tuple[list[StremioStream], list[RankedStream]]:
        """Drop dead hoster links among the top ``max_probe_count`` streams."""
        assert self._probe_fn is not None
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
        keep = [i in alive_indices or i >= limit for i in range(len(ranked))]
        return (
            [s for s, k in zip(streams, keep) if k],
            [r for r, k in zip(ranked, keep) if k],
        )

    async def _resolve_top_streams(
        self,
        ranked: list[RankedStream],
        deadline: float,
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
        and what is resolved so far is returned.

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
                    return idx, await self._resolve_fn(r.url, r.hoster)
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

        while pending:
            remaining = deadline - time.monotonic()
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
            video_count += self._collect_resolved(done, ranked, resolved_map)
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
            deadline_hit=timed_out,
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
