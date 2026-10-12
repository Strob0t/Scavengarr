"""Hoster resolution of one Stremio request, started as results arrive.

The plugins deliver results one by one; resolving each hoster's best link
as soon as it is known puts the first streams near the search's first
results instead of after the whole search (measure 2 of
``docs/plans/round5-measures.md``). ``ResolveFlow`` decides when the
request's answer is due; a search from the cache answers with the
resolver's cached outcomes and resolves its other links in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import math
import time
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial
from typing import Any, Protocol

import structlog

from scavengarr.application.stremio.search_progress import SearchProgress
from scavengarr.application.stremio.stream_builder import (
    hoster_key,
    is_direct_video_url,
)
from scavengarr.domain.entities.stremio import RankedStream, ResolvedStream
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.browser_fetcher import PageClaim, page_claim
from scavengarr.domain.ports.telemetry import TelemetryPort

log = structlog.get_logger(__name__)

# Resolves a hoster embed URL (url, hoster hint) to a playable video URL
ResolveCallback = Callable[[str, str], Awaitable[ResolvedStream | None]]

# The cached outcome of resolving a URL, without resolving it: (True, stream),
# (True, None) for a link cached as dead, (False, None) when not cached.
CachedResolutionCallback = Callable[[str], tuple[bool, ResolvedStream | None]]

# The streams of search results, best first (results, plugin languages)
RankFn = Callable[[list[SearchResult], dict[str, str]], Awaitable[list[RankedStream]]]

# Cached answers resolve their other links in the background one title at a
# time: 17 cached titles asked within seconds started 17 runs at once, and 12
# Filemoon resolutions queued for the stealth browser's 2 pages until their
# 10 s timeout, which opened its breaker (dev-server end-to-end run, 2026-10-05)
_BACKGROUND_RUNS = 1


class HosterResolution:
    """The resolutions of one request's ranked streams.

    Each hoster (and language, ``hoster_key``) resolves its best-ranked link
    that is not known dead; its next link only after that failed, none while
    one runs or after one resolved. Resolving every candidate at once opened
    dozens of connections to distinct CDNs within a second, which the home
    router blocked like a port scan. A better-ranked link that arrives later
    resolves too. Each URL resolves once, at most *concurrency* at a time,
    among the top *limit* streams. *changed* is set whenever a resolution
    ends. The resolutions run under *claim* (the stealth browser hands out
    its pages by it).
    """

    def __init__(
        self,
        resolve_fn: ResolveCallback,
        *,
        concurrency: int,
        limit: int,
        changed: asyncio.Event,
        claim: PageClaim | None = None,
    ) -> None:
        self._resolve_fn = resolve_fn
        self._claim = claim
        self._semaphore = asyncio.Semaphore(concurrency)
        self._limit = limit
        self._changed = changed
        self._ranked: list[RankedStream] = []
        self._hosters: list[list[int]] = []  # indices into _ranked, rank order
        self._tasks: dict[str, asyncio.Task[ResolvedStream | None]] = {}
        self._closed = False

    @property
    def ranked(self) -> list[RankedStream]:
        """The streams considered: the top of the latest ranking."""
        return self._ranked

    @property
    def started(self) -> int:
        return len(self._tasks)

    @property
    def unfinished(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    def update(self, ranked: list[RankedStream]) -> None:
        """Take a new ranking (more results) and start what is due.

        New results only push streams down: a resolution whose stream left
        the top *limit* can no longer be part of the answer and is
        cancelled, so the answer does not wait for it and its slot frees.
        """
        self._ranked = ranked[: self._limit]
        considered = {stream.url for stream in self._ranked}
        for url, task in self._tasks.items():
            if url not in considered:
                task.cancel()
        groups: dict[object, list[int]] = {}
        for i, stream in enumerate(self._ranked):
            groups.setdefault(hoster_key(stream) or stream.url, []).append(i)
        self._hosters = list(groups.values())
        self._start_due()

    def pending(self) -> bool:
        """Whether a resolution runs or is due (the due ones start)."""
        self._start_due()
        return self.unfinished > 0

    def resolved(self) -> dict[int, ResolvedStream]:
        """Each hoster's best resolution (a video before an echoed embed URL),
        by index into :attr:`ranked`."""
        chosen: dict[int, ResolvedStream] = {}
        for indices in self._hosters:
            results = [(i, r) for i in indices if (r := self._result(i)) is not None]
            videos = [(i, r) for i, r in results if self._is_video(i, r)]
            if best := videos or results:
                i, resolved = best[0]
                chosen[i] = resolved
        return chosen

    def videos(self) -> int:
        """How many hosters have a video."""
        return sum(self._is_video(i, r) for i, r in self.resolved().items())

    async def aclose(self) -> None:
        """Cancel the resolutions that still run; start no new ones."""
        self._closed = True
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    def _start_due(self) -> None:
        """Start each hoster's best link that is neither known dead nor tried.

        Repeats until nothing more starts: under an eager task factory a
        cached outcome ends inside ``create_task``, and a dead one makes the
        hoster's next link due at once.
        """
        started = True
        while started and not self._closed:
            started = False
            for indices in self._hosters:
                for i in indices:
                    url = self._ranked[i].url
                    task = self._tasks.get(url)
                    if task is None:
                        self._tasks[url] = self._start(self._ranked[i])
                        started = True
                        break
                    if not task.done() or self._result(i) is not None:
                        break

    def _start(self, stream: RankedStream) -> asyncio.Task[ResolvedStream | None]:
        task = asyncio.create_task(self._resolve(stream))
        task.add_done_callback(self._ended)
        return task

    async def _resolve(self, stream: RankedStream) -> ResolvedStream | None:
        if self._claim is not None:
            # The task's own context: the claim ends with it
            page_claim.set(self._claim)
        if stream.source_plugin:
            # Its log lines name the plugin (hoster_without_resolver)
            structlog.contextvars.bind_contextvars(plugin=stream.source_plugin)
        async with self._semaphore:
            try:
                return await self._resolve_fn(stream.url, stream.hoster)
            except Exception:
                log.debug(
                    "stremio_resolve_failed",
                    hoster=stream.hoster,
                    url=stream.url[:80],
                    exc_info=True,
                )
                return None

    def _ended(self, _task: asyncio.Task[ResolvedStream | None]) -> None:
        if self._closed:
            return
        self._start_due()
        self._changed.set()

    def _result(self, index: int) -> ResolvedStream | None:
        task = self._tasks.get(self._ranked[index].url)
        if task is None or not task.done() or task.cancelled():
            return None
        return task.result()

    def _is_video(self, index: int, resolved: ResolvedStream) -> bool:
        return is_direct_video_url(resolved, self._ranked[index].url)


class ResolveConfig(Protocol):
    """The answer rule's limits."""

    max_probe_count: int
    probe_concurrency: int
    resolve_target_count: int
    stream_deadline_seconds: float


# A search handed over for the background resolution: its progress, the
# plugins' languages and the resolver
_HandOver = tuple[SearchProgress, dict[str, str], ResolveCallback]


class ResolveFlow:
    """When a request's streams resolve, and when its answer is due.

    A search from the cache answers at once with the resolutions the
    resolver has cached; any other search's links resolve as its results
    arrive (``HosterResolution``) until the answer is due.
    """

    def __init__(
        self,
        *,
        rank: RankFn,
        cached_resolution_fn: CachedResolutionCallback | None,
        telemetry: TelemetryPort,
        config: ResolveConfig,
        spawn: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]],
    ) -> None:
        self._rank = rank
        self._cached_resolution_fn = cached_resolution_fn
        self._telemetry = telemetry
        self._max_probe_count = config.max_probe_count
        self._probe_concurrency = config.probe_concurrency
        self._resolve_target = config.resolve_target_count
        self._deadline_s = config.stream_deadline_seconds
        # The use case's task registry: its aclose() cancels the background
        # resolutions
        self._spawn = spawn
        # Resolutions of a cached answer's other links, one per cache key,
        # one cache key at a time
        self._background_resolutions: dict[str, asyncio.Task[None]] = {}
        # Per cache key, the hand-overs waiting for the key's run to end
        self._next_runs: dict[str, list[_HandOver]] = {}
        self._background_runs = asyncio.Semaphore(_BACKGROUND_RUNS)

    async def resolve(
        self,
        progress: SearchProgress,
        plugin_languages: dict[str, str],
        resolve_fn: ResolveCallback,
        *,
        deadline: float,
        budget_ends: float = math.inf,
        key: str,
        from_cache: bool,
    ) -> tuple[list[RankedStream], dict[int, ResolvedStream]]:
        """The ranked streams and their resolutions, by index.

        Results from the search cache go out at once with the resolutions in
        the resolver's cache when one of them is a video. The links without
        a cached outcome resolve in the background for the next request,
        one run per cache key at a time (such answers waited for the resolve
        grace, 4.1-4.4 s, for one link resolved for the first time); with
        none, no run starts. Otherwise the streams resolve as
        ``_resolve_as_results_arrive`` describes, with the search's results
        up to *budget_ends* (the answer budget).
        """
        cached_fn = self._cached_resolution_fn
        if from_cache and cached_fn is not None:
            ranked = await self._rank(progress.results, plugin_languages)
            ranked = ranked[: self._max_probe_count]
            cached = self._cached_resolutions(ranked, cached_fn)
            if any(is_direct_video_url(r, ranked[i].url) for i, r in cached.items()):
                log.info("stremio_resolve_from_cache", resolved=len(cached))
                self._telemetry.count("stremio_phase", "cached", phase="resolve")
                if any(not cached_fn(s.url)[0] for s in ranked):
                    self.resolve_in_background(
                        key, progress, plugin_languages, resolve_fn
                    )
                return ranked, cached
        return await self._resolve_as_results_arrive(
            progress,
            plugin_languages,
            resolve_fn,
            deadline=deadline,
            budget_ends=budget_ends,
        )

    def resolve_in_background(
        self,
        key: str,
        progress: SearchProgress,
        plugin_languages: dict[str, str],
        resolve_fn: ResolveCallback,
    ) -> bool:
        """Resolve *progress*'s links in the background for the next request,
        as its results arrive: a cached answer's links without a cached
        outcome, or the results of a search that goes on after the answer
        (a continuing search, a refresh, a completion). One run per cache
        key at a time: a hand-over while the key's run runs waits for it
        and starts when it ends, in order; cached answers (all alike) wait
        once. Returns whether a run started now.
        """
        if key in self._background_resolutions:
            queue = self._next_runs.setdefault(key, [])
            if progress.done:
                queue[:] = [queued for queued in queue if not queued[0].done]
            queue.append((progress, plugin_languages, resolve_fn))
            return False
        task = self._spawn(
            self._resolve_in_background(progress, plugin_languages, resolve_fn)
        )
        self._background_resolutions[key] = task
        task.add_done_callback(partial(self._run_done, key))
        return True

    def _run_done(self, key: str, task: asyncio.Task[None]) -> None:
        """The key's run ended: the next hand-over waiting for it starts (not
        at shutdown, which cancels the runs)."""
        self._background_resolutions.pop(key, None)
        queue = self._next_runs.pop(key, [])
        if task.cancelled() or not queue:
            return
        hand_over, *rest = queue
        if rest:
            self._next_runs[key] = rest
        self.resolve_in_background(key, *hand_over)

    async def _resolve_in_background(
        self,
        progress: SearchProgress,
        plugin_languages: dict[str, str],
        resolve_fn: ResolveCallback,
    ) -> None:
        """Resolve a search's links for the next request, as they arrive.

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
        dead, the echoed embed URLs and the links not resolved yet: a plugin
        that answered after the first answer can rank a new link of a hoster
        first, and the hoster's stream of that answer dropped out (the
        background resolution resolves the new link for the next request);
        an echo is dropped from the answer, and it left the hoster out for
        the hour it stayed cached (code review, 2026-10-06).
        """
        hosters: dict[object, list[int]] = {}
        for i, stream in enumerate(ranked):
            hosters.setdefault(hoster_key(stream) or stream.url, []).append(i)
        found: dict[int, ResolvedStream] = {}
        for indices in hosters.values():
            for i in indices:
                _, resolved = cached_fn(ranked[i].url)
                if resolved is not None and is_direct_video_url(
                    resolved, ranked[i].url
                ):
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
        budget_ends: float = math.inf,
        background: bool = False,
    ) -> tuple[list[RankedStream], dict[int, ResolvedStream]]:
        """Resolve the search's links while it runs; stop when the answer is due.

        Each new batch of results is ranked with the ones before and handed
        to a ``HosterResolution`` (one link per hoster at a time, rank
        order). Results arriving after *budget_ends* (the answer budget,
        ``time.monotonic()``) are not taken, they go to the cache for the
        next request; a request past the budget from the start (a retry)
        takes the results known then. The answer is due when
        ``resolve_target_count``
        hosters have a video, or when the search is done (or past the
        budget) and no resolution runs or is due, at the latest at
        *deadline*; resolutions still running then are cancelled. In the
        *background* (no answer waits) there is no target: every hoster
        resolves until done or the deadline, which fills the resolver's
        cache.
        """
        changed = asyncio.Event()
        resolution = HosterResolution(
            resolve_fn,
            concurrency=self._probe_concurrency,
            limit=self._max_probe_count,
            changed=changed,
            claim=PageClaim("background" if background else "capture", deadline),
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
                    now = time.monotonic()
                    past_budget = now >= budget_ends
                    if seen < len(progress.results) and not (past_budget and seen):
                        new = progress.results[seen:]
                        seen += len(new)
                        # Score only the new streams: re-sorting all of them
                        # per batch scored every stream again (10 ms per
                        # request on x86 for 200). The stable merge keeps the
                        # full sort's order
                        ranked = list(
                            heapq.merge(
                                ranked,
                                await self._rank(new, plugin_languages),
                                key=lambda s: -s.rank_score,
                            )
                        )
                        resolution.update(ranked)
                        continue
                    if target > 0 and resolution.videos() >= target:
                        reason = "target"
                        break
                    if (progress.done or past_budget) and not resolution.pending():
                        reason = "done" if progress.done else "budget"
                        break
                    remaining = deadline - now
                    if remaining <= 0:
                        break
                    if not past_budget:
                        # Wake at the budget to stop taking results
                        remaining = min(remaining, budget_ends - now)
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
