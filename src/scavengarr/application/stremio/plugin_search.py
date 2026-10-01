"""Run plugin searches for Stremio requests.

Fans a set of queries out over a set of plugins within the global
concurrency budget, with per-plugin timeout, an optional shared deadline,
circuit breaker, metrics,
episode filtering and result validation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
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
    def record_success(self, name: str) -> None: ...
    def record_failure(self, name: str) -> None: ...


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
        """
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

        try:
            return await asyncio.wait_for(
                self._search_single_plugin(
                    name, query, category, season=season, episode=episode
                ),
                timeout=timeout,
            )
        except TimeoutError:
            # A plugin that had at least half its timeout counts as failing
            # (dead hosts always run into the deadline and must still trip
            # the breaker); one that queued for most of the budget does not
            counts = timeout >= self._plugin_timeout / 2
            log.warning(
                "stremio_plugin_timeout",
                plugin=name,
                timeout=round(timeout, 2),
                cut_by_deadline=timeout < self._plugin_timeout,
            )
            if counts and self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(breaker_key)
            return []

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
            # _run_plugin_with_timeout records that case instead.
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
