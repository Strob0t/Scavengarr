"""Run plugin searches for Stremio requests.

Fans a set of queries out over a set of plugins within the global
concurrency budget, with per-plugin timeout, an optional shared deadline,
circuit breaker, metrics,
episode filtering and result validation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from contextvars import ContextVar
from typing import Any, Protocol

import structlog

from scavengarr.domain.entities.scoring import PluginScoreSnapshot
from scavengarr.domain.plugins.base import (
    PluginProtocol,
    ResultKey,
    SearchResult,
    result_key,
)
from scavengarr.domain.ports.concurrency import ConcurrencyBudgetPort
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.plugin_score_store import PluginScoreStorePort
from scavengarr.domain.ports.search_engine import SearchEnginePort
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort

log = structlog.get_logger(__name__)

# A mirror member's plugin score counts from this confidence on, as for the
# scored plugin selection; a member without one ranks like a new snapshot
_MIN_CONFIDENCE = 0.1
_NEUTRAL_SCORE = 0.5


class CircuitBreaker(Protocol):
    """Circuit breaker per key (skip after N consecutive failures)."""

    def allow(self, name: str) -> bool: ...
    def is_closed(self, name: str) -> bool: ...
    def record_success(self, name: str) -> None: ...
    def record_failure(self, name: str) -> None: ...


class PluginHealth(Protocol):
    """Whether a plugin's site answered its last periodic check."""

    def is_reachable(self, name: str) -> bool: ...


async def current_snapshots(
    store: PluginScoreStorePort, names: Sequence[str], category: int
) -> dict[str, PluginScoreSnapshot]:
    """The ``current`` score snapshots of the plugins that have one.

    A snapshot the store fails to read counts as none
    (``plugin_scores_unreadable``): scores only rank the plugins, so a
    failing store must not fail the request.
    """
    snapshots = await asyncio.gather(
        *(store.get_snapshot(name, category, "current") for name in names),
        return_exceptions=True,
    )
    found: dict[str, PluginScoreSnapshot] = {}
    failed: list[str] = []
    errors: set[str] = set()
    for name, snapshot in zip(names, snapshots, strict=True):
        if isinstance(snapshot, BaseException):
            failed.append(name)
            errors.add(repr(snapshot))
        elif snapshot is not None:
            found[name] = snapshot
    if failed:
        log.warning("plugin_scores_unreadable", plugins=failed, errors=sorted(errors))
    return found


def _breaker_key(name: str, category: int | None) -> str:
    """Circuit breaker entry of a plugin: one per requested category.

    A site can be too slow for one content type only (kinoking's movie
    pages take 12-17 s, its series pages 1 s): its movies must not cost
    every movie request the search budget while its series keep coming.
    """
    return name if category is None else f"{name}:{category}"


EpisodeFilterFn = Callable[
    [list[SearchResult], int | None, int | None], list[SearchResult]
]

# Callback type for warming up a shared Playwright browser.
# Returns (browser, playwright) tuple — opaque at this layer.
BrowserWarmupFn = Callable[[], Coroutine[Any, Any, tuple[Any, Any]]]

# Receives one plugin's results (of one query) as soon as they are there
OnResultsFn = Callable[[list[SearchResult]], Awaitable[None]]


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
        telemetry: TelemetryPort = NO_TELEMETRY,
        circuit_breaker: CircuitBreaker | None = None,
        browser_warmup_fn: BrowserWarmupFn | None = None,
        mirror_groups: Mapping[str, str] | None = None,
        plugin_health: PluginHealth | None = None,
        score_store: PluginScoreStorePort | None = None,
    ) -> None:
        self._plugins = plugins
        self._search_engine = search_engine
        self._episode_filter_fn = episode_filter_fn
        self._max_results_var = max_results_var
        self._plugin_timeout = plugin_timeout
        self._max_results_per_plugin = max_results_per_plugin
        self._telemetry = telemetry
        self._circuit_breaker = circuit_breaker
        self._browser_warmup_fn = browser_warmup_fn
        # Plugin name -> mirror group: sites serving one database
        self._mirror_groups = mirror_groups or {}
        self._plugin_health = plugin_health
        # Plugin scores pick a mirror group's member
        self._score_store = score_store
        # Breaker keys of mirror members that gave nothing while their
        # standby delivered: they rank behind the other members
        self._mirror_misses: set[str] = set()

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
        on_results: OnResultsFn | None = None,
    ) -> list[SearchResult]:
        """Search plugins with all query variants, deduplicate results.

        Concurrency is managed by the global *budget* (from
        ConcurrencyPool) which provides fair-share httpx + PW slots.

        When a browser warmup function is configured, a fire-and-forget
        warmup task starts the shared Chromium process in the background
        while httpx plugins search.  Playwright plugins obtain the
        shared browser from their injected pool reference (set at
        composition time).

        Each result goes to *on_results* and into the returned list once
        (``result_key``): the full and the base title find many results
        of a plugin twice, and the title filter scored them twice (code
        review, 2026-10-06).

        *deadline* (``time.monotonic()`` value) ends the whole search: a
        plugin still waiting for a slot then is skipped, a running one is
        cut at the deadline instead of after its own full timeout.
        *on_results* gets each plugin's new results of each query as soon
        as they are there.

        Plugins whose site failed the periodic health check are skipped
        (before a mirror group picks its member).
        """
        plugin_names = self._reachable(plugin_names)
        scores = await self._mirror_scores(plugin_names, category)
        plugin_names, standbys = self._one_per_mirror_group(
            plugin_names, category, scores
        )

        # --- Fire-and-forget pre-warm for shared Playwright browser ---
        if self._browser_warmup_fn is not None:
            task = asyncio.create_task(
                self._browser_warmup_fn(),
                name="browser-warmup",
            )
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )

        found: list[SearchResult] = []
        seen: set[ResultKey] = set()

        async def _hand_on(results: list[SearchResult]) -> None:
            new: list[SearchResult] = []
            for result in results:
                key = result_key(result)
                if key not in seen:
                    seen.add(key)
                    new.append(result)
            found.extend(new)
            if new and on_results is not None:
                await on_results(new)

        await asyncio.gather(
            *(
                self.search_plugins(
                    plugin_names,
                    q,
                    category,
                    season=season,
                    episode=episode,
                    budget=budget,
                    deadline=deadline,
                    on_results=_hand_on,
                    standbys=standbys,
                )
                for q in queries
            )
        )
        return found

    def _reachable(self, plugin_names: list[str]) -> list[str]:
        """The plugins whose site answered its last health check."""
        health = self._plugin_health
        if health is None:
            return plugin_names
        unreachable = [n for n in plugin_names if not health.is_reachable(n)]
        if unreachable:
            log.info("stremio_plugins_unreachable", plugins=unreachable)
        for name in unreachable:
            self._telemetry.count("plugin_search", "unreachable", plugin=name)
        return [n for n in plugin_names if n not in unreachable]

    async def _mirror_scores(
        self, plugin_names: list[str], category: int | None
    ) -> dict[str, float]:
        """The plugin scores of the mirror-group members, where confident.

        A failing score store leaves the members in their given order.
        """
        members = [name for name in plugin_names if name in self._mirror_groups]
        store = self._score_store
        if store is None or category is None or not members:
            return {}
        snapshots = await current_snapshots(store, members, category)
        return {
            name: snapshot.final_score
            for name, snapshot in snapshots.items()
            if snapshot.confidence > _MIN_CONFIDENCE
        }

    def _one_per_mirror_group(
        self,
        plugin_names: list[str],
        category: int | None,
        scores: Mapping[str, float],
    ) -> tuple[list[str], dict[str, str]]:
        """Keep one plugin per mirror group: the best-ranked member with a
        closed breaker; the next one is its standby (member -> standby).

        Mirrors front one database (hdfilme, streamcloud, streamkiste):
        asking all of them triples the work for the same streams. The member
        with the best plugin score (the scoring subsystem's health and search
        probes) is asked, without scores the first one; a member that gave
        nothing while its standby delivered ranks behind the others. When no
        member's breaker is closed, all stay in and their breakers decide,
        so a half-open probe can bring one back.
        """
        if not self._mirror_groups:
            return plugin_names, {}

        def rank(name: str) -> tuple[bool, float]:
            missed = _breaker_key(name, category) in self._mirror_misses
            return missed, -scores.get(name, _NEUTRAL_SCORE)

        chosen: dict[str, str] = {}
        standbys: dict[str, str] = {}
        breaker = self._circuit_breaker
        for name in sorted(plugin_names, key=rank):
            group = self._mirror_groups.get(name)
            if group is None:
                continue
            if breaker is not None and not breaker.is_closed(
                _breaker_key(name, category)
            ):
                continue
            if group not in chosen:
                chosen[group] = name
            elif chosen[group] not in standbys:
                standbys[chosen[group]] = name

        def keep(name: str) -> bool:
            group = self._mirror_groups.get(name)
            return group not in chosen or chosen[group] == name

        skipped = [name for name in plugin_names if not keep(name)]
        if skipped:
            log.info("stremio_mirrors_skipped", chosen=chosen, skipped=skipped)
        return [name for name in plugin_names if keep(name)], standbys

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
        on_results: OnResultsFn | None = None,
        standbys: Mapping[str, str] | None = None,
    ) -> list[SearchResult]:
        """Search all plugins in parallel with bounded concurrency.

        Uses the global concurrency pool's fair-share budget to manage
        httpx and Playwright slot allocation across requests. Each
        plugin's results go to *on_results* once it has given its slot back.
        A plugin that gives nothing (no hits, error, timeout) hands the
        query to its standby in *standbys* (a mirror of its database).
        """

        async def _run(name: str) -> list[SearchResult]:
            if self._plugins.get_mode(name) == "playwright":
                slot = budget.acquire_pw()
            else:
                slot = budget.acquire_httpx()
            async with slot:
                return await self._run_plugin_with_timeout(
                    name,
                    query,
                    category,
                    season=season,
                    episode=episode,
                    deadline=deadline,
                )

        async def _search_one(name: str) -> list[SearchResult]:
            results = await _run(name)
            standby = (standbys or {}).get(name)
            if standby is not None and not results:
                results = await _run(standby)
                log.info(
                    "stremio_mirror_standby",
                    plugin=name,
                    standby=standby,
                    result_count=len(results),
                )
                # Two empty answers agree (the title is not there); a
                # delivering standby outranks the member from now on
                if results:
                    self._mirror_misses.add(_breaker_key(name, category))
                    self._mirror_misses.discard(_breaker_key(standby, category))
            elif standby is not None:
                self._mirror_misses.discard(_breaker_key(name, category))
            if results and on_results is not None:
                await on_results(results)
            return results

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
                self._telemetry.count("plugin_search", "skipped", plugin=name)
                return []

        # Circuit breaker: skip plugins that have been failing consistently
        breaker_key = _breaker_key(name, category)
        if self._circuit_breaker is not None and not self._circuit_breaker.allow(
            breaker_key
        ):
            log.info("stremio_plugin_circuit_open", plugin=name, category=category)
            self._telemetry.count("plugin_search", "breaker_open", plugin=name)
            return []

        try:
            return await asyncio.wait_for(
                self._search_single_plugin(
                    name, query, category, season=season, episode=episode
                ),
                timeout=timeout,
            )
        except TimeoutError:
            log.warning(
                "stremio_plugin_timeout",
                plugin=name,
                timeout=round(timeout, 2),
                cut_by_deadline=timeout < self._plugin_timeout,
            )
            # A plugin that had at least half its timeout counts as failing
            # (dead hosts always run into the deadline and must still trip
            # the breaker); one that queued for most of the budget does not
            counts = timeout >= self._plugin_timeout / 2
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

        success = False
        cancelled = False
        results: list[SearchResult] = []
        with self._telemetry.stage("plugin_search", plugin=name) as stage:
            try:
                token = self._max_results_var.set(self._max_results_per_plugin)
                try:
                    raw = await self._dispatch_search(
                        plugin, query, category, season=season, episode=episode
                    )
                finally:
                    self._max_results_var.reset(token)
                raw = await asyncio.to_thread(
                    self._episode_filter_fn, raw, season, episode
                )
                results = await self._search_engine.validate_results(raw)
                success = True
                stage.outcome = "hits" if results else "empty"
            except Exception:
                log.warning("stremio_plugin_search_error", plugin=name, exc_info=True)
                stage.outcome = "error"
                results = []
            except BaseException:
                cancelled = True
                log.warning("stremio_plugin_search_cancelled", plugin=name)
                raise
            finally:
                # Record circuit breaker outcome — but NOT on cancellation
                # (BaseException), since the timeout handler in
                # _run_plugin_with_timeout records that case instead.
                if not cancelled:
                    self._record_outcome(
                        _breaker_key(name, category),
                        success=success,
                        found=bool(results),
                    )
        if results:
            self._telemetry.record("plugin_results", len(results), plugin=name)

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
