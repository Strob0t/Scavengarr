"""Run plugin searches for Stremio requests.

Fans a set of queries out over a set of plugins within the global
concurrency budget, with per-plugin timeout, an optional shared deadline,
circuit breaker, metrics,
episode filtering and result validation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

import structlog

from scavengarr.domain.plugins.base import PluginProtocol, SearchResult
from scavengarr.domain.ports.concurrency import ConcurrencyBudgetPort
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.search_engine import SearchEnginePort

log = structlog.get_logger(__name__)


class CircuitBreaker(Protocol):
    """Circuit breaker per key (skip after N consecutive failures)."""

    def allow(self, name: str) -> bool: ...
    def is_closed(self, name: str) -> bool: ...
    def record_success(self, name: str) -> None: ...
    def record_failure(self, name: str) -> None: ...


class PluginHealth(Protocol):
    """Whether a plugin's site answered its last periodic check."""

    def is_reachable(self, name: str) -> bool: ...


def _breaker_key(name: str, category: int | None) -> str:
    """Circuit breaker entry of a plugin: one per requested category.

    A site can be too slow for one content type only (kinoking's movie
    pages take 12-17 s, its series pages 1 s): its movies must not cost
    every movie request the search budget while its series keep coming.
    """
    return name if category is None else f"{name}:{category}"


class PluginSearchMetrics(Protocol):
    """Records per-plugin search metrics."""

    def record_plugin_search(
        self,
        name: str,
        duration_ns: int,
        result_count: int,
        *,
        success: bool,
    ) -> None: ...


EpisodeFilterFn = Callable[
    [list[SearchResult], int | None, int | None], list[SearchResult]
]

# Callback type for warming up a shared Playwright browser.
# Returns (browser, playwright) tuple — opaque at this layer.
BrowserWarmupFn = Callable[[], Coroutine[Any, Any, tuple[Any, Any]]]


@dataclass(frozen=True)
class LateSearch:
    """A plugin search the deadline cut that runs on (``finish_late``)."""

    plugin: str
    task: asyncio.Task[list[SearchResult]]

    @property
    def results(self) -> list[SearchResult]:
        """The results once the search has finished, else none."""
        task = self.task
        if not task.done() or task.cancelled() or task.exception() is not None:
            return []
        return task.result()


async def finish_late(late: list[LateSearch], *, timeout: float) -> None:
    """Wait up to *timeout* seconds for the late searches, then cancel the rest.

    They are cancelled as well when the caller is cancelled while waiting.
    """
    tasks = [s.task for s in late]
    if not tasks:
        return
    try:
        await asyncio.wait(tasks, timeout=max(timeout, 0.0))
    finally:
        await _cancel(*(task for task in tasks if not task.done()))


async def _cancel(*tasks: asyncio.Task[list[SearchResult]]) -> None:
    """Cancel *tasks* and wait until they have stopped.

    A cancellation of the caller itself still propagates (``gather``).
    """
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class PluginSearchRunner:
    """Search plugins in parallel and collect validated results."""

    def __init__(
        self,
        *,
        plugins: PluginRegistryPort,
        search_engine: SearchEnginePort,
        episode_filter_fn: EpisodeFilterFn,
        max_results_var: ContextVar[int | None],
        plugin_timeout: float,
        max_results_per_plugin: int,
        metrics: PluginSearchMetrics | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        browser_warmup_fn: BrowserWarmupFn | None = None,
        mirror_groups: Mapping[str, str] | None = None,
        plugin_health: PluginHealth | None = None,
    ) -> None:
        self._plugins = plugins
        self._search_engine = search_engine
        self._episode_filter_fn = episode_filter_fn
        self._max_results_var = max_results_var
        self._plugin_timeout = plugin_timeout
        self._max_results_per_plugin = max_results_per_plugin
        self._metrics = metrics
        self._circuit_breaker = circuit_breaker
        self._browser_warmup_fn = browser_warmup_fn
        # Plugin name -> mirror group: sites serving one database
        self._mirror_groups = mirror_groups or {}
        self._plugin_health = plugin_health

    async def search_with_fallback(
        self,
        plugin_names: list[str],
        queries: list[str],
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
        budget: ConcurrencyBudgetPort,
        deadline: float | None = None,
        late: list[LateSearch] | None = None,
    ) -> list[SearchResult]:
        """Search plugins with all query variants, deduplicate results.

        Concurrency is managed by the global *budget* (from
        ConcurrencyPool) which provides fair-share httpx + PW slots.

        When a browser warmup function is configured, a fire-and-forget
        warmup task starts the shared Chromium process in the background
        while httpx plugins search.  Playwright plugins obtain the
        shared browser from their injected pool reference (set at
        composition time).

        The first query's results are always kept in full.  Subsequent
        (fallback) queries only add results whose ``download_link`` was
        not already seen, to avoid duplicates from the same plugin
        matching on both the full title and the shorter base title.

        *deadline* (``time.monotonic()`` value) ends the whole search: a
        plugin still waiting for a slot then is skipped, a running one is
        cut at the deadline instead of after its own full timeout.

        Plugins whose site failed the periodic health check are skipped
        (before a mirror group picks its member).
        """
        plugin_names = self._reachable(plugin_names)
        plugin_names = self._one_per_mirror_group(plugin_names, category)

        # --- Fire-and-forget pre-warm for shared Playwright browser ---
        if self._browser_warmup_fn is not None:
            task = asyncio.create_task(
                self._browser_warmup_fn(),
                name="browser-warmup",
            )
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )

        search_tasks = [
            self.search_plugins(
                plugin_names,
                q,
                category,
                season=season,
                episode=episode,
                budget=budget,
                deadline=deadline,
                late=late,
            )
            for q in queries
        ]
        results_per_query = await asyncio.gather(*search_tasks)

        # First query's results are kept unconditionally.
        all_results: list[SearchResult] = list(results_per_query[0])
        if len(results_per_query) > 1:
            seen: set[str] = {r.download_link for r in all_results}
            for results in results_per_query[1:]:
                for r in results:
                    if r.download_link not in seen:
                        seen.add(r.download_link)
                        all_results.append(r)
        return all_results

    def _reachable(self, plugin_names: list[str]) -> list[str]:
        """The plugins whose site answered its last health check."""
        health = self._plugin_health
        if health is None:
            return plugin_names
        unreachable = [n for n in plugin_names if not health.is_reachable(n)]
        if unreachable:
            log.info("stremio_plugins_unreachable", plugins=unreachable)
        return [n for n in plugin_names if n not in unreachable]

    def _one_per_mirror_group(
        self, plugin_names: list[str], category: int | None
    ) -> list[str]:
        """Keep one plugin per mirror group: the first with a closed breaker.

        Mirrors front one database (hdfilme, streamcloud, streamkiste):
        asking all of them triples the work for the same streams. When no
        member's breaker is closed, all stay in and their breakers decide,
        so a half-open probe can bring one back.
        """
        if not self._mirror_groups:
            return plugin_names
        chosen: dict[str, str] = {}
        for name in plugin_names:
            group = self._mirror_groups.get(name)
            if group is None or group in chosen:
                continue
            breaker = self._circuit_breaker
            if breaker is None or breaker.is_closed(_breaker_key(name, category)):
                chosen[group] = name

        def keep(name: str) -> bool:
            group = self._mirror_groups.get(name)
            return group not in chosen or chosen[group] == name

        skipped = [name for name in plugin_names if not keep(name)]
        if skipped:
            log.info("stremio_mirrors_skipped", chosen=chosen, skipped=skipped)
        return [name for name in plugin_names if keep(name)]

    async def search_plugins(
        self,
        plugin_names: list[str],
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
        budget: ConcurrencyBudgetPort,
        deadline: float | None = None,
        late: list[LateSearch] | None = None,
    ) -> list[SearchResult]:
        """Search all plugins in parallel with bounded concurrency.

        Uses the global concurrency pool's fair-share budget to manage
        httpx and Playwright slot allocation across requests.
        """

        async def _search_one(name: str) -> list[SearchResult]:
            is_pw = self._plugins.get_mode(name) == "playwright"
            if is_pw:
                async with budget.acquire_pw():
                    return await self._run_plugin_with_timeout(
                        name,
                        query,
                        category,
                        season=season,
                        episode=episode,
                        deadline=deadline,
                        late=late,
                    )
            else:
                async with budget.acquire_httpx():
                    return await self._run_plugin_with_timeout(
                        name,
                        query,
                        category,
                        season=season,
                        episode=episode,
                        deadline=deadline,
                        late=late,
                    )

        tasks = [_search_one(name) for name in plugin_names]
        results_per_plugin = await asyncio.gather(*tasks)

        all_results: list[SearchResult] = []
        for results in results_per_plugin:
            all_results.extend(results)
        return all_results

    async def _run_plugin_with_timeout(
        self,
        name: str,
        query: str,
        category: int | None,
        *,
        season: int | None = None,
        episode: int | None = None,
        deadline: float | None = None,
        late: list[LateSearch] | None = None,
    ) -> list[SearchResult]:
        """Run a single plugin search with timeout, catching errors."""
        timeout = self._plugin_timeout
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                log.info("stremio_plugin_skipped_deadline", plugin=name)
                return []

        # Circuit breaker: skip plugins that have been failing consistently
        breaker_key = _breaker_key(name, category)
        if self._circuit_breaker is not None and not self._circuit_breaker.allow(
            breaker_key
        ):
            log.info("stremio_plugin_circuit_open", plugin=name, category=category)
            return []

        task = asyncio.ensure_future(
            self._search_single_plugin(
                name, query, category, season=season, episode=episode
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            log.warning(
                "stremio_plugin_timeout",
                plugin=name,
                timeout=round(timeout, 2),
                cut_by_deadline=timeout < self._plugin_timeout,
                runs_on=late is not None,
            )
            if late is not None:
                # Not failing yet: it fails if still running when the late
                # plugins are cut, its answer reports like any other
                task.add_done_callback(partial(self._late_search_done, breaker_key))
                late.append(LateSearch(name, task))
                return []
            # A plugin that had at least half its timeout counts as failing
            # (dead hosts always run into the deadline and must still trip
            # the breaker); one that queued for most of the budget does not
            counts = timeout >= self._plugin_timeout / 2
            if counts and self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(breaker_key)
            await _cancel(task)
            return []
        except asyncio.CancelledError:
            await _cancel(task)
            raise

    @staticmethod
    async def _dispatch_search(
        plugin: PluginProtocol,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Dispatch to isolated_search() when available, else search()."""
        isolated_search: Callable[..., Awaitable[list[SearchResult]]] | None = getattr(
            plugin, "isolated_search", None
        )
        if isolated_search is not None:
            return await isolated_search(
                query, category, season=season, episode=episode
            )
        return await plugin.search(
            query, category=category, season=season, episode=episode
        )

    def _record_outcome(self, key: str, *, success: bool, found: bool) -> None:
        """Report a finished search to the circuit breaker.

        An empty answer proves nothing: kinoking answers a search without
        hits at once but needs 12-17 s per movie page, and its empty answers
        kept resetting the breaker.
        """
        if self._circuit_breaker is None:
            return
        if not success:
            self._circuit_breaker.record_failure(key)
        elif found:
            self._circuit_breaker.record_success(key)

    def _late_search_done(
        self, key: str, task: asyncio.Future[list[SearchResult]]
    ) -> None:
        """Report a late search cut at the end of its extra time as failing.

        A late search that ends by itself reports in _search_single_plugin.
        """
        if task.cancelled() and self._circuit_breaker is not None:
            self._circuit_breaker.record_failure(key)

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

        The plugin is called directly (with the max_results context so it
        limits pagination), then its results are episode-filtered and
        validated via the SearchEngine.
        """
        try:
            plugin = self._plugins.get(name)
        except Exception:
            log.warning("stremio_plugin_not_found", plugin=name, exc_info=True)
            return []

        t0 = time.perf_counter_ns()
        success = False
        cancelled = False
        results: list[SearchResult] = []
        try:
            token = self._max_results_var.set(self._max_results_per_plugin)
            try:
                raw = await self._dispatch_search(
                    plugin, query, category, season=season, episode=episode
                )
            finally:
                self._max_results_var.reset(token)
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None, self._episode_filter_fn, raw, season, episode
            )
            results = await self._search_engine.validate_results(raw)
            success = True
        except Exception:
            log.warning("stremio_plugin_search_error", plugin=name, exc_info=True)
            results = []
        except BaseException:
            cancelled = True
            log.warning("stremio_plugin_search_cancelled", plugin=name)
            raise
        finally:
            duration_ns = time.perf_counter_ns() - t0
            if self._metrics is not None:
                self._metrics.record_plugin_search(
                    name,
                    duration_ns,
                    len(results),
                    success=success,
                )
            # Record circuit breaker outcome — but NOT on cancellation
            # (BaseException), since the timeout handler in
            # _run_plugin_with_timeout (for a late search _late_search_done)
            # records that case instead.
            if not cancelled:
                self._record_outcome(
                    _breaker_key(name, category), success=success, found=bool(results)
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
