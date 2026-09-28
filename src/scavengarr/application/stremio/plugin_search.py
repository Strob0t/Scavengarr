"""Run plugin searches for Stremio requests.

Fans a set of queries out over a set of plugins within the global
concurrency budget, with per-plugin timeout, circuit breaker, metrics,
episode filtering and result validation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Protocol

import structlog

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.concurrency import ConcurrencyBudgetPort
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.search_engine import SearchEnginePort

log = structlog.get_logger(__name__)


class CircuitBreaker(Protocol):
    """Per-plugin circuit breaker (skip after N consecutive failures)."""

    def allow(self, name: str) -> bool: ...
    def record_success(self, name: str) -> None: ...
    def record_failure(self, name: str) -> None: ...


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
BrowserWarmupFn = Callable[[], Awaitable[tuple[Any, Any]]]


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
                        name, query, category, season=season, episode=episode
                    )
            else:
                async with budget.acquire_httpx():
                    return await self._run_plugin_with_timeout(
                        name, query, category, season=season, episode=episode
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
    ) -> list[SearchResult]:
        """Run a single plugin search with timeout, catching errors."""
        # Circuit breaker: skip plugins that have been failing consistently
        if self._circuit_breaker is not None and not self._circuit_breaker.allow(name):
            log.debug("stremio_plugin_circuit_open", plugin=name)
            return []

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
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(name)
            return []

    @staticmethod
    async def _dispatch_search(
        plugin: object,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Dispatch to isolated_search() when available, else search()."""
        if hasattr(plugin, "isolated_search") and callable(plugin.isolated_search):
            return await plugin.isolated_search(
                query, category, season=season, episode=episode
            )
        return await plugin.search(
            query, category=category, season=season, episode=episode
        )

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
            log.warning("stremio_plugin_not_found", plugin=name, exc_info=True)
            return []

        t0 = time.perf_counter_ns()
        success = False
        cancelled = False
        results: list[SearchResult] = []
        try:
            if (
                hasattr(plugin, "search")
                and callable(plugin.search)
                and not hasattr(plugin, "scraping")
            ):
                # Python plugin: call directly, validate results
                # Set max_results context so plugins limit pagination
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
            else:
                # YAML plugin: delegate to search engine
                results = await self._search_engine.search(
                    plugin, query, category=category
                )
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
            if self._circuit_breaker is not None and not cancelled:
                if success:
                    self._circuit_breaker.record_success(name)
                else:
                    self._circuit_breaker.record_failure(name)

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
