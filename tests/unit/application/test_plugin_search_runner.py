"""Tests for PluginSearchRunner (plugin fan-out for Stremio requests)."""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from scavengarr.application.stremio.plugin_search import PluginSearchRunner
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.concurrency import ConcurrencyPool

_max_results_var: ContextVar[int | None] = ContextVar(
    "runner_test_max_results", default=None
)


def _sr(link: str, title: str = "Movie") -> SearchResult:
    return SearchResult(title=title, download_link=link)


def _plugin(results: list[SearchResult] | Exception) -> MagicMock:
    """Python plugin mock with search() and no scraping stages."""
    plugin = MagicMock(spec=["search"])
    if isinstance(results, Exception):
        plugin.search = AsyncMock(side_effect=results)
    else:
        plugin.search = AsyncMock(return_value=results)
    return plugin


def _registry(plugins: dict[str, MagicMock], mode: str = "httpx") -> MagicMock:
    registry = MagicMock()
    registry.get.side_effect = lambda name: plugins[name]
    registry.get_mode.return_value = mode
    return registry


def _runner(registry: MagicMock, **overrides: object) -> PluginSearchRunner:
    engine = AsyncMock()
    engine.validate_results = AsyncMock(side_effect=lambda r: r)
    kwargs: dict[str, object] = {
        "plugins": registry,
        "search_engine": engine,
        "episode_filter_fn": lambda results, _season, _episode: results,
        "max_results_var": _max_results_var,
        "plugin_timeout": 5.0,
        "max_results_per_plugin": 42,
    }
    kwargs.update(overrides)
    return PluginSearchRunner(**kwargs)  # type: ignore[arg-type]


async def _search(
    runner: PluginSearchRunner, names: list[str], queries: list[str]
) -> list[SearchResult]:
    pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
    async with pool.request() as budget:
        return await runner.search_with_fallback(names, queries, 2000, budget=budget)


class TestSearchWithFallback:
    async def test_collects_results_from_all_plugins(self) -> None:
        registry = _registry(
            {"a": _plugin([_sr("https://a/1")]), "b": _plugin([_sr("https://b/1")])}
        )

        results = await _search(_runner(registry), ["a", "b"], ["movie"])

        assert {r.download_link for r in results} == {"https://a/1", "https://b/1"}

    async def test_fallback_query_adds_only_unseen_links(self) -> None:
        plugin = _plugin([])
        plugin.search = AsyncMock(
            side_effect=[
                [_sr("https://x/1")],
                [_sr("https://x/1"), _sr("https://x/2")],
            ]
        )
        registry = _registry({"a": plugin})

        results = await _search(_runner(registry), ["a"], ["full title", "base"])

        assert [r.download_link for r in results] == ["https://x/1", "https://x/2"]

    async def test_browser_warmup_fired(self) -> None:
        warmup = AsyncMock(return_value=(MagicMock(), MagicMock()))
        registry = _registry({"a": _plugin([_sr("https://a/1")])})

        await _search(_runner(registry, browser_warmup_fn=warmup), ["a"], ["q"])
        await asyncio.sleep(0)

        warmup.assert_awaited_once()


class TestSinglePlugin:
    async def test_tags_source_plugin(self) -> None:
        registry = _registry({"kinox": _plugin([_sr("https://k/1")])})

        results = await _search(_runner(registry), ["kinox"], ["q"])

        assert results[0].metadata["source_plugin"] == "kinox"

    async def test_sets_max_results_context_during_search(self) -> None:
        seen: list[int | None] = []

        async def _search_fn(*_args: object, **_kwargs: object) -> list[SearchResult]:
            seen.append(_max_results_var.get())
            return []

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_search_fn)

        await _search(_runner(_registry({"a": plugin})), ["a"], ["q"])

        assert seen == [42]
        assert _max_results_var.get() is None

    async def test_applies_episode_filter(self) -> None:
        calls: list[tuple[int | None, int | None]] = []

        def _filter(
            results: list[SearchResult], season: int | None, episode: int | None
        ) -> list[SearchResult]:
            calls.append((season, episode))
            return results[:1]

        registry = _registry({"a": _plugin([_sr("https://a/1"), _sr("https://a/2")])})
        runner = _runner(registry, episode_filter_fn=_filter)
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            results = await runner.search_plugins(
                ["a"], "q", 5000, season=2, episode=3, budget=budget
            )

        assert calls == [(2, 3)]
        assert len(results) == 1

    async def test_plugin_error_returns_empty_and_records_failure(self) -> None:
        breaker = MagicMock()
        breaker.allow.return_value = True
        metrics = MagicMock()
        registry = _registry({"a": _plugin(RuntimeError("boom"))})
        runner = _runner(registry, circuit_breaker=breaker, metrics=metrics)

        assert await _search(runner, ["a"], ["q"]) == []
        breaker.record_failure.assert_called_once_with("a:2000")
        breaker.record_success.assert_not_called()
        assert metrics.record_plugin_search.call_args.kwargs == {"success": False}

    async def test_success_recorded(self) -> None:
        breaker = MagicMock()
        breaker.allow.return_value = True
        registry = _registry({"a": _plugin([_sr("https://a/1")])})

        await _search(_runner(registry, circuit_breaker=breaker), ["a"], ["q"])

        breaker.record_success.assert_called_once_with("a:2000")

    async def test_unknown_plugin_returns_empty(self) -> None:
        registry = MagicMock()
        registry.get.side_effect = KeyError("missing")
        registry.get_mode.return_value = "httpx"

        assert await _search(_runner(registry), ["missing"], ["q"]) == []


class TestCircuitBreakerAndTimeout:
    async def test_breaker_tracks_plugin_per_category(self) -> None:
        # A site that is too slow for movies (kinoking: 12-17 s movie pages)
        # must not cost every movie request the search budget, while its
        # series keep coming
        async def _search_by_category(
            _query: str, category: int | None = None, **_kwargs: object
        ) -> list[SearchResult]:
            if category == 2000:
                await asyncio.sleep(10)
            return [_sr(f"https://a/{category}")]

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_search_by_category)
        breaker = PluginCircuitBreaker(failure_threshold=1)
        runner = _runner(
            _registry({"a": plugin}), circuit_breaker=breaker, plugin_timeout=0.05
        )
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)

        async with pool.request() as budget:
            movies = await runner.search_plugins(["a"], "q", 2000, budget=budget)
            series = await runner.search_plugins(["a"], "q", 5000, budget=budget)
            movies_again = await runner.search_plugins(["a"], "q", 2000, budget=budget)

        assert movies == [] and movies_again == []
        assert [r.download_link for r in series] == ["https://a/5000"]
        assert plugin.search.await_count == 2

    async def test_empty_answer_does_not_reset_the_breaker(self) -> None:
        # kinoking answers a search without hits at once but needs 12-17 s
        # for a movie page: its empty answers kept closing the breaker
        async def _search(query: str, **_kwargs: object) -> list[SearchResult]:
            if query == "hit":
                await asyncio.sleep(10)
            return []

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_search)
        breaker = PluginCircuitBreaker(failure_threshold=2)
        runner = _runner(
            _registry({"a": plugin}), circuit_breaker=breaker, plugin_timeout=0.05
        )
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)

        async with pool.request() as budget:
            for query in ("hit", "no hit", "hit", "hit"):
                await runner.search_plugins(["a"], query, 2000, budget=budget)

        assert plugin.search.await_count == 3

    async def test_open_circuit_skips_plugin(self) -> None:
        plugin = _plugin([_sr("https://a/1")])
        breaker = MagicMock()
        breaker.allow.return_value = False
        runner = _runner(_registry({"a": plugin}), circuit_breaker=breaker)

        assert await _search(runner, ["a"], ["q"]) == []
        plugin.search.assert_not_awaited()

    async def test_timeout_records_single_failure(self) -> None:
        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await asyncio.sleep(10)
            return []

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_slow)
        breaker = MagicMock()
        breaker.allow.return_value = True
        runner = _runner(
            _registry({"a": plugin}), circuit_breaker=breaker, plugin_timeout=0.01
        )

        assert await _search(runner, ["a"], ["q"]) == []
        breaker.record_failure.assert_called_once_with("a:2000")

    async def test_deadline_cuts_queued_plugins_without_failure(self) -> None:
        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await asyncio.sleep(10)
            return []

        plugins = {name: _plugin([]) for name in ("a", "b", "c")}
        for plugin in plugins.values():
            plugin.search = AsyncMock(side_effect=_slow)
        breaker = MagicMock()
        breaker.allow.return_value = True
        runner = _runner(_registry(plugins), circuit_breaker=breaker)

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        started = time.monotonic()
        async with pool.request() as budget:
            results = await runner.search_with_fallback(
                list(plugins), ["q"], 2000, budget=budget, deadline=started + 0.2
            )

        assert results == []
        assert time.monotonic() - started < 1.0
        breaker.record_failure.assert_not_called()

    async def test_deadline_timeout_counts_when_plugin_had_half_its_time(
        self,
    ) -> None:
        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await asyncio.sleep(10)
            return []

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_slow)
        breaker = MagicMock()
        breaker.allow.return_value = True
        runner = _runner(
            _registry({"a": plugin}), circuit_breaker=breaker, plugin_timeout=0.3
        )

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, deadline=time.monotonic() + 0.2
            )

        breaker.record_failure.assert_called_once_with("a:2000")

    async def test_plugin_is_skipped_after_deadline(self) -> None:
        plugin = _plugin([_sr("https://a/1")])
        runner = _runner(_registry({"a": plugin}))

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            results = await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, deadline=time.monotonic() - 1
            )

        assert results == []
        plugin.search.assert_not_awaited()


class TestDispatch:
    async def test_prefers_isolated_search(self) -> None:
        plugin = MagicMock(spec=["search", "isolated_search"])
        plugin.search = AsyncMock(return_value=[])
        plugin.isolated_search = AsyncMock(return_value=[_sr("https://iso/1")])

        results = await _search(_runner(_registry({"a": plugin})), ["a"], ["q"])

        assert [r.download_link for r in results] == ["https://iso/1"]
        plugin.search.assert_not_awaited()

    @pytest.mark.parametrize("mode", ["httpx", "playwright"])
    async def test_uses_budget_for_both_modes(self, mode: str) -> None:
        registry = _registry({"a": _plugin([_sr("https://a/1")])}, mode=mode)

        results = await _search(_runner(registry), ["a"], ["q"])

        assert len(results) == 1


class TestMirrorGroups:
    """hdfilme, streamcloud and streamkiste serve one database: a Stremio
    request asks one of them, the next one when its breaker opens."""

    _GROUPS = {"hdfilme": "hdfilme", "streamcloud": "hdfilme"}  # noqa: RUF012

    def _plugins(self) -> dict[str, MagicMock]:
        return {
            "hdfilme": _plugin([_sr("https://dood/1")]),
            "streamcloud": _plugin([_sr("https://dood/1")]),
            "other": _plugin([_sr("https://voe/1")]),
        }

    async def test_first_member_alone(self) -> None:
        plugins = self._plugins()
        runner = _runner(_registry(plugins), mirror_groups=self._GROUPS)

        await _search(runner, ["hdfilme", "streamcloud", "other"], ["q"])

        assert plugins["hdfilme"].search.await_count == 1
        assert plugins["streamcloud"].search.await_count == 0
        assert plugins["other"].search.await_count == 1

    async def test_next_member_when_the_breaker_is_open(self) -> None:
        plugins = self._plugins()
        breaker = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        breaker.record_failure("hdfilme:2000")
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, circuit_breaker=breaker
        )

        await _search(runner, ["hdfilme", "streamcloud", "other"], ["q"])

        assert plugins["hdfilme"].search.await_count == 0
        assert plugins["streamcloud"].search.await_count == 1

    async def test_all_members_open_stay_in_for_the_probes(self) -> None:
        """Without a closed member every one goes to its breaker, whose
        half-open probe can bring it back."""
        plugins = self._plugins()
        breaker = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        breaker.record_failure("hdfilme:2000")
        breaker.record_failure("streamcloud:2000")
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, circuit_breaker=breaker
        )

        await _search(runner, ["hdfilme", "streamcloud"], ["q"])

        assert plugins["hdfilme"].search.await_count == 1
        assert plugins["streamcloud"].search.await_count == 1


class _Health:
    def __init__(self, *down: str) -> None:
        self._down = set(down)

    def is_reachable(self, name: str) -> bool:
        return name not in self._down


class TestPluginHealth:
    """Plugins whose site failed the periodic check are not searched: such
    plugins held every first answer to the deadline (fifth round)."""

    async def test_an_unreachable_plugin_is_not_searched(self) -> None:
        plugins = {
            "up": _plugin([_sr("https://a/1")]),
            "down": _plugin([_sr("https://b/1")]),
        }
        runner = _runner(_registry(plugins), plugin_health=_Health("down"))

        with capture_logs() as logs:
            results = await _search(runner, ["up", "down"], ["q"])

        assert [r.download_link for r in results] == ["https://a/1"]
        assert plugins["down"].search.await_count == 0
        skipped = [e for e in logs if e["event"] == "stremio_plugins_unreachable"]
        assert [e["plugins"] for e in skipped] == [["down"]]

    async def test_a_mirror_group_picks_a_reachable_member(self) -> None:
        plugins = TestMirrorGroups()._plugins()
        runner = _runner(
            _registry(plugins),
            mirror_groups=TestMirrorGroups._GROUPS,
            plugin_health=_Health("hdfilme"),
        )

        await _search(runner, ["hdfilme", "streamcloud", "other"], ["q"])

        assert plugins["hdfilme"].search.await_count == 0
        assert plugins["streamcloud"].search.await_count == 1


def _endless_search(cancelled: asyncio.Event) -> AsyncMock:
    """search() that never ends and sets *cancelled* when cancelled."""

    async def _search(*_args: object, **_kwargs: object) -> list[SearchResult]:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return []

    return AsyncMock(side_effect=_search)


class TestOnResults:
    """Each plugin's results go out as soon as they are there: the request
    resolves links while slower plugins still search."""

    async def test_a_fast_plugins_results_come_before_a_slow_one_ends(self) -> None:
        slow_done = asyncio.Event()

        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await asyncio.sleep(0.1)
            slow_done.set()
            return [_sr("https://b/1")]

        slow = _plugin([])
        slow.search = AsyncMock(side_effect=_slow)
        runner = _runner(_registry({"a": _plugin([_sr("https://a/1")]), "b": slow}))
        reported: list[tuple[list[str], bool]] = []

        async def _on_results(results: list[SearchResult]) -> None:
            reported.append(([r.download_link for r in results], slow_done.is_set()))

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a", "b"], ["q"], 2000, budget=budget, on_results=_on_results
            )

        assert reported == [(["https://a/1"], False), (["https://b/1"], True)]

    async def test_empty_answers_are_not_reported(self) -> None:
        runner = _runner(_registry({"a": _plugin([])}))
        on_results = AsyncMock()

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, on_results=on_results
            )

        on_results.assert_not_awaited()


class TestDeadlineCut:
    async def test_a_plugin_the_deadline_cuts_is_cancelled(self) -> None:
        cancelled = asyncio.Event()
        plugin = _plugin([])
        plugin.search = _endless_search(cancelled)
        runner = _runner(_registry({"a": plugin}))

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            results = await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, deadline=time.monotonic() + 0.05
            )

        assert results == []
        assert cancelled.is_set()
