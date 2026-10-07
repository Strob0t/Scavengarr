"""Tests for PluginSearchRunner (plugin fan-out for Stremio requests)."""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from structlog.testing import capture_logs

from scavengarr.application.stremio.plugin_search import PluginSearchRunner
from scavengarr.domain.entities.scoring import PluginScoreSnapshot
from scavengarr.domain.plugins.base import PluginUnreachableError, SearchResult
from scavengarr.domain.ports.browser_fetcher import PageClaim, page_claim
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.telemetry import Telemetry

_max_results_var: ContextVar[int | None] = ContextVar(
    "runner_test_max_results", default=None
)


def _sr(link: str, title: str = "Movie") -> SearchResult:
    return SearchResult(title=title, download_link=link)


def _plugin(results: list[SearchResult] | Exception) -> MagicMock:
    """Plugin mock with search() only."""
    plugin = MagicMock(spec=["search"])
    if isinstance(results, Exception):
        plugin.search = AsyncMock(side_effect=results)
    else:
        plugin.search = AsyncMock(return_value=results)
    return plugin


def _answer_after(delay: float, results: list[SearchResult]) -> AsyncMock:
    """search() answering *results* after *delay* seconds."""

    async def _search(*_args: object, **_kwargs: object) -> list[SearchResult]:
        await asyncio.sleep(delay)
        return results

    return AsyncMock(side_effect=_search)


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

    async def test_on_results_gets_each_result_once(self) -> None:
        """The full and the base title find many results twice; the title
        filter scored them twice (code review, 2026-10-06)."""
        plugin = _plugin([])
        plugin.search = AsyncMock(
            side_effect=[
                [_sr("https://x/1")],
                [_sr("https://x/1"), _sr("https://x/2")],
            ]
        )
        batches: list[list[str]] = []

        async def _on_results(results: list[SearchResult]) -> None:
            batches.append([r.download_link for r in results])

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await _runner(_registry({"a": plugin})).search_with_fallback(
                ["a"],
                ["full title", "base"],
                2000,
                budget=budget,
                on_results=_on_results,
            )

        assert batches == [["https://x/1"], ["https://x/2"]]

    async def test_another_plugins_result_with_the_same_link_stays(self) -> None:
        """Its list of links can hold other hosters."""
        registry = _registry(
            {"a": _plugin([_sr("https://x/1")]), "b": _plugin([_sr("https://x/1")])}
        )

        results = await _search(_runner(registry), ["a", "b"], ["full title", "base"])

        assert sorted(r.metadata["source_plugin"] for r in results) == ["a", "b"]

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

    async def test_the_episode_filter_keeps_the_log_context(self) -> None:
        """It runs in a worker thread; its log lines keep the request_id."""
        seen: dict[str, object] = {}

        def _filter(
            results: list[SearchResult], season: int | None, episode: int | None
        ) -> list[SearchResult]:
            seen.update(structlog.contextvars.get_contextvars())
            return results

        registry = _registry({"a": _plugin([_sr("https://a/1")])})
        runner = _runner(registry, episode_filter_fn=_filter)
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        with structlog.contextvars.bound_contextvars(request_id="r1"):
            async with pool.request() as budget:
                await runner.search_plugins(
                    ["a"], "q", 5000, season=2, episode=3, budget=budget
                )

        assert seen["request_id"] == "r1"

    async def test_plugin_error_returns_empty_and_records_failure(self) -> None:
        breaker = MagicMock()
        breaker.allow.return_value = True
        registry = _registry({"a": _plugin(RuntimeError("boom"))})
        runner = _runner(registry, circuit_breaker=breaker)

        assert await _search(runner, ["a"], ["q"]) == []
        breaker.record_failure.assert_called_once_with("a:2000")
        breaker.record_success.assert_not_called()

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

    async def test_a_timeout_past_the_budget_counts_as_a_failure(self) -> None:
        """The plugin had its whole timeout, so a dead host trips the breaker
        whether or not the request's budget passed meanwhile."""
        plugin = _plugin([])
        plugin.search = _answer_after(10, [])
        breaker = MagicMock()
        breaker.allow.return_value = True
        runner = _runner(
            _registry({"a": plugin}), circuit_breaker=breaker, plugin_timeout=0.05
        )

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, budget_ends=time.monotonic() - 1
            )

        breaker.record_failure.assert_called_once_with("a:2000")


class TestPluginRecord:
    """The long-term record counts what the runner did (ideas backlog, N2)."""

    async def test_searches_and_results_are_counted(self) -> None:
        history = MagicMock(spec=["count"])
        registry = _registry(
            {
                "a": _plugin([_sr("https://a/1"), _sr("https://a/2")]),
                "b": _plugin([]),
            }
        )

        await _search(_runner(registry, history=history), ["a", "b"], ["q"])

        assert sorted(c.args for c in history.count.call_args_list) == [
            ("a", "results", 2),
            ("a", "searches"),
            ("b", "searches"),
        ]

    async def test_a_timeout_counts_as_a_search_and_a_timeout(self) -> None:
        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await asyncio.sleep(10)
            return []

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_slow)
        history = MagicMock(spec=["count"])
        runner = _runner(_registry({"a": plugin}), plugin_timeout=0.01, history=history)

        await _search(runner, ["a"], ["q"])

        assert [c.args for c in history.count.call_args_list] == [
            ("a", "searches"),
            ("a", "timeouts"),
        ]

    async def test_a_skipped_plugin_is_not_counted(self) -> None:
        breaker = MagicMock()
        breaker.allow.return_value = False
        history = MagicMock(spec=["count"])
        runner = _runner(
            _registry({"a": _plugin([])}), circuit_breaker=breaker, history=history
        )

        await _search(runner, ["a"], ["q"])

        history.count.assert_not_called()

    async def test_dropped_results_are_counted(self) -> None:
        """The results the episode filter dropped, per plugin."""
        history = MagicMock(spec=["count"])
        registry = _registry({"a": _plugin([_sr("https://a/1"), _sr("https://a/2")])})
        runner = _runner(
            registry,
            history=history,
            episode_filter_fn=lambda results, _season, _episode: results[:1],
        )

        await _search(runner, ["a"], ["q"])

        assert ("a", "dropped", 1) in [c.args for c in history.count.call_args_list]


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


class TestMirrorStandby:
    """A member whose search gives nothing hands the request to the next
    member: a parser broken by a theme change answers empty, which trips no
    breaker, so the group never failed over (code review, 2026-10-06). A
    member that gave nothing while the next one delivered ranks behind the
    others from then on."""

    _GROUPS = {  # noqa: RUF012
        "hdfilme": "hdfilme",
        "streamcloud": "hdfilme",
        "streamkiste": "hdfilme",
    }

    @staticmethod
    def _plugins(*empty: str) -> dict[str, MagicMock]:
        return {
            name: _plugin([] if name in empty else [_sr("https://dood/1")])
            for name in ("hdfilme", "streamcloud", "streamkiste")
        }

    @staticmethod
    async def _searched(
        runner: PluginSearchRunner, plugins: dict[str, MagicMock]
    ) -> list[str]:
        for plugin in plugins.values():
            plugin.search.reset_mock()
        await _search(runner, list(plugins), ["q"])
        return [n for n, p in plugins.items() if p.search.await_count]

    async def test_the_next_member_searches_when_the_first_finds_nothing(
        self,
    ) -> None:
        plugins = self._plugins("hdfilme")
        runner = _runner(_registry(plugins), mirror_groups=self._GROUPS)

        with capture_logs() as logs:
            results = await _search(runner, list(plugins), ["q"])

        assert [r.download_link for r in results] == ["https://dood/1"]
        assert plugins["streamkiste"].search.await_count == 0
        standby = [e for e in logs if e["event"] == "stremio_mirror_standby"]
        assert [(e["plugin"], e["standby"]) for e in standby] == [
            ("hdfilme", "streamcloud")
        ]

    async def test_the_member_that_missed_ranks_behind(self) -> None:
        plugins = self._plugins("hdfilme")
        runner = _runner(_registry(plugins), mirror_groups=self._GROUPS)
        await self._searched(runner, plugins)

        assert await self._searched(runner, plugins) == ["streamcloud"]

    async def test_the_ranking_follows_the_member_that_delivers(self) -> None:
        """hdfilme misses, then streamcloud: hdfilme ranks first again."""
        plugins = self._plugins("hdfilme")
        runner = _runner(
            _registry(plugins), mirror_groups={"hdfilme": "g", "streamcloud": "g"}
        )
        await self._searched(runner, plugins)
        plugins["hdfilme"].search.return_value = [_sr("https://dood/1")]
        plugins["streamcloud"].search.return_value = []

        assert await self._searched(runner, plugins) == [
            "hdfilme",
            "streamcloud",
            "streamkiste",
        ]
        assert await self._searched(runner, plugins) == ["hdfilme", "streamkiste"]

    async def test_a_member_that_delivers_alone_ranks_first_again(self) -> None:
        """hdfilme misses, then delivers while streamcloud's breaker is
        open (no standby): once streamcloud is back, hdfilme ranks first."""
        plugins = self._plugins("hdfilme")
        breaker = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        runner = _runner(
            _registry(plugins),
            mirror_groups={"hdfilme": "g", "streamcloud": "g"},
            circuit_breaker=breaker,
        )
        await self._searched(runner, plugins)
        plugins["hdfilme"].search.return_value = [_sr("https://dood/1")]
        breaker.record_failure("streamcloud:2000")
        assert await self._searched(runner, plugins) == ["hdfilme", "streamkiste"]
        breaker.reset("streamcloud:2000")

        assert await self._searched(runner, plugins) == ["hdfilme", "streamkiste"]

    async def test_an_absent_title_costs_two_searches(self) -> None:
        """Two empty answers agree: the title is not in the database, and
        the ranking stays."""
        plugins = self._plugins("hdfilme", "streamcloud", "streamkiste")
        runner = _runner(_registry(plugins), mirror_groups=self._GROUPS)

        assert await self._searched(runner, plugins) == ["hdfilme", "streamcloud"]
        assert await self._searched(runner, plugins) == ["hdfilme", "streamcloud"]

    async def test_a_failing_member_hands_over_too(self) -> None:
        plugins = self._plugins()
        plugins["hdfilme"].search.side_effect = RuntimeError("theme changed")
        runner = _runner(_registry(plugins), mirror_groups=self._GROUPS)

        results = await _search(runner, list(plugins), ["q"])

        assert [r.download_link for r in results] == ["https://dood/1"]
        assert plugins["streamcloud"].search.await_count == 1

    async def test_the_standby_has_a_closed_breaker(self) -> None:
        plugins = self._plugins("hdfilme")
        breaker = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        breaker.record_failure("streamcloud:2000")
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, circuit_breaker=breaker
        )

        assert await self._searched(runner, plugins) == ["hdfilme", "streamkiste"]


def _scores(**members: tuple[float, float]) -> AsyncMock:
    """Score store with (final_score, confidence) per plugin."""

    async def _snapshot(
        plugin: str, category: int, bucket: str
    ) -> PluginScoreSnapshot | None:
        if plugin not in members:
            return None
        score, confidence = members[plugin]
        return PluginScoreSnapshot(
            plugin=plugin,
            category=category,
            bucket="current",
            final_score=score,
            confidence=confidence,
        )

    store = AsyncMock()
    store.get_snapshot = AsyncMock(side_effect=_snapshot)
    return store


class TestScoredMirrorGroups:
    """A mirror group asks its member with the best plugin score (the
    scoring subsystem's health and search probes), not the first one."""

    _GROUPS = {  # noqa: RUF012
        "hdfilme": "hdfilme",
        "streamcloud": "hdfilme",
        "streamkiste": "hdfilme",
    }

    def _plugins(self) -> dict[str, MagicMock]:
        return {
            name: _plugin([_sr("https://dood/1")])
            for name in ("hdfilme", "streamcloud", "streamkiste", "other")
        }

    async def _searched(self, runner: PluginSearchRunner, plugins: dict) -> list[str]:
        await _search(runner, list(plugins), ["q"])
        return [n for n, p in plugins.items() if p.search.await_count]

    async def test_the_best_scored_member_is_asked(self) -> None:
        plugins = self._plugins()
        store = _scores(hdfilme=(0.4, 0.5), streamcloud=(0.9, 0.5))
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, score_store=store
        )

        assert await self._searched(runner, plugins) == ["streamcloud", "other"]

    async def test_a_member_without_a_score_beats_a_bad_one(self) -> None:
        """No score is the neutral 0.5 of a new snapshot."""
        plugins = self._plugins()
        store = _scores(hdfilme=(0.3, 0.5), streamcloud=(0.4, 0.5))
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, score_store=store
        )

        assert await self._searched(runner, plugins) == ["streamkiste", "other"]

    async def test_an_unconfident_score_counts_as_none(self) -> None:
        plugins = self._plugins()
        store = _scores(hdfilme=(0.3, 0.5), streamcloud=(0.9, 0.05))
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, score_store=store
        )

        assert await self._searched(runner, plugins) == ["streamcloud", "other"]

    async def test_the_best_member_with_an_open_breaker_gives_way(self) -> None:
        plugins = self._plugins()
        store = _scores(hdfilme=(0.4, 0.5), streamcloud=(0.9, 0.5))
        breaker = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        breaker.record_failure("streamcloud:2000")
        runner = _runner(
            _registry(plugins),
            mirror_groups=self._GROUPS,
            score_store=store,
            circuit_breaker=breaker,
        )

        assert await self._searched(runner, plugins) == ["streamkiste", "other"]

    async def test_a_failing_score_store_keeps_the_first_member(self) -> None:
        plugins = self._plugins()
        store = AsyncMock()
        store.get_snapshot = AsyncMock(side_effect=ConnectionError("redis down"))
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, score_store=store
        )

        assert await self._searched(runner, plugins) == ["hdfilme", "other"]

    async def test_only_mirror_members_are_looked_up(self) -> None:
        plugins = self._plugins()
        store = _scores()
        runner = _runner(
            _registry(plugins), mirror_groups=self._GROUPS, score_store=store
        )

        await self._searched(runner, plugins)

        looked_up = {c.args[0] for c in store.get_snapshot.await_args_list}
        assert looked_up == {"hdfilme", "streamcloud", "streamkiste"}


class _Health:
    def __init__(self, *down: str) -> None:
        self._down = set(down)
        self.marked: list[str] = []

    def is_reachable(self, name: str) -> bool:
        return name not in self._down

    def mark_unreachable(self, name: str) -> None:
        self.marked.append(name)


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

    async def test_a_plugin_without_a_reachable_domain_is_marked(self) -> None:
        """Its own domain check found no domain: the monitor skips it until
        the recheck, and the record counts it (ideas backlog step 21)."""
        plugins = {
            "up": _plugin([_sr("https://a/1")]),
            "down": _plugin(PluginUnreachableError("down")),
        }
        health = _Health()
        history = MagicMock(spec=["count"])
        runner = _runner(_registry(plugins), plugin_health=health, history=history)

        with capture_logs() as logs:
            results = await _search(runner, ["up", "down"], ["q"])

        assert [r.download_link for r in results] == ["https://a/1"]
        assert health.marked == ["down"]
        counted = [c.args for c in history.count.call_args_list]
        assert ("down", "unreachable") in counted
        events = [e for e in logs if e["event"] == "stremio_plugin_unreachable"]
        assert [e["plugin"] for e in events] == ["down"]

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


class TestBudget:
    """The request's answer budget cuts no plugin (continue-cut-searches):
    a plugin's work past it reaches the cache and the next request."""

    async def test_a_plugin_holding_its_slot_past_the_budget_returns(self) -> None:
        plugin = _plugin([])
        plugin.search = _answer_after(0.1, [_sr("https://a/1")])
        runner = _runner(_registry({"a": plugin}))

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            results = await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, budget_ends=time.monotonic() + 0.02
            )

        assert [r.download_link for r in results] == ["https://a/1"]

    async def test_a_plugin_queued_at_the_budget_runs(self) -> None:
        """One slot: b waits for a's 0.1 s, the budget ends before it has
        the slot, and it runs with its full timeout nevertheless."""
        a = _plugin([])
        a.search = _answer_after(0.1, [])
        b = _plugin([_sr("https://b/1")])
        runner = _runner(_registry({"a": a, "b": b}))

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            results = await runner.search_with_fallback(
                ["a", "b"],
                ["q"],
                2000,
                budget=budget,
                budget_ends=time.monotonic() + 0.02,
            )

        assert [r.download_link for r in results] == ["https://b/1"]

    async def test_a_plugin_is_cut_at_its_own_timeout_not_at_the_budget(self) -> None:
        cancelled = asyncio.Event()
        plugin = _plugin([])
        plugin.search = _endless_search(cancelled)
        runner = _runner(_registry({"a": plugin}), plugin_timeout=0.2)
        started = time.monotonic()

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            results = await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, budget_ends=started + 0.05
            )

        assert results == []
        assert cancelled.is_set()
        assert time.monotonic() - started >= 0.2


class TestTelemetry:
    """Every plugin search is recorded in the runner, not in the plugins."""

    def _sample(self, t: Telemetry, outcome: str, plugin: str = "a") -> float | None:
        return t.registry.get_sample_value(
            "scavengarr_plugin_search_total", {"plugin": plugin, "outcome": outcome}
        )

    def _timed(self, t: Telemetry, plugin: str = "a") -> float | None:
        return t.registry.get_sample_value(
            "scavengarr_plugin_search_seconds_count", {"plugin": plugin}
        )

    @pytest.mark.parametrize(
        ("answer", "outcome"),
        [
            ([_sr("https://a/1"), _sr("https://a/2")], "hits"),
            ([], "empty"),
            (RuntimeError("boom"), "error"),
            (PluginUnreachableError("a"), "unreachable"),
        ],
    )
    async def test_outcome_and_duration(
        self, answer: list[SearchResult] | Exception, outcome: str
    ) -> None:
        t = Telemetry()
        runner = _runner(_registry({"a": _plugin(answer)}), telemetry=t)

        await _search(runner, ["a"], ["q"])

        assert self._sample(t, outcome) == 1
        assert self._timed(t) == 1

    async def test_results_are_counted(self) -> None:
        t = Telemetry()
        answer = [_sr("https://a/1"), _sr("https://a/2")]
        runner = _runner(_registry({"a": _plugin(answer)}), telemetry=t)

        await _search(runner, ["a"], ["q"])

        results = t.registry.get_sample_value(
            "scavengarr_plugin_results_total", {"plugin": "a"}
        )
        assert results == 2

    async def test_a_plugin_returning_after_the_budget_is_late(self) -> None:
        t = Telemetry()
        runner = _runner(_registry({"a": _plugin([_sr("https://a/1")])}), telemetry=t)

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, budget_ends=time.monotonic() - 1
            )

        assert self._sample(t, "late") == 1
        assert self._sample(t, "hits") is None
        assert self._timed(t) == 1

    async def test_open_breaker_counts_without_duration(self) -> None:
        t = Telemetry()
        breaker = MagicMock()
        breaker.allow.return_value = False
        runner = _runner(
            _registry({"a": _plugin([])}), circuit_breaker=breaker, telemetry=t
        )

        await _search(runner, ["a"], ["q"])

        assert self._sample(t, "breaker_open") == 1
        assert self._timed(t) is None

    async def test_unreachable_plugin_is_counted(self) -> None:
        t = Telemetry()
        plugins = {"up": _plugin([]), "down": _plugin([])}
        runner = _runner(_registry(plugins), plugin_health=_Health("down"), telemetry=t)

        await _search(runner, ["up", "down"], ["q"])

        assert self._sample(t, "unreachable", plugin="down") == 1
        assert self._sample(t, "empty", plugin="up") == 1


class TestPluginDone:
    """Each plugin's end is reported once; finished unless cut or failed,
    so the search's entry knows what is missing (continue-cut-searches)."""

    @pytest.mark.parametrize(
        ("answer", "finished"),
        [
            ([_sr("https://a/1")], True),
            ([], True),
            (RuntimeError("boom"), False),
        ],
    )
    async def test_outcome(
        self, answer: list[SearchResult] | Exception, finished: bool
    ) -> None:
        done = AsyncMock()
        runner = _runner(_registry({"a": _plugin(answer)}))

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, on_plugin_done=done
            )

        done.assert_awaited_once_with("a", finished, 0)

    async def test_a_timeout_is_not_finished(self) -> None:
        plugin = _plugin([])
        plugin.search = _answer_after(10, [])
        done = AsyncMock()
        runner = _runner(_registry({"a": plugin}), plugin_timeout=0.01)

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, on_plugin_done=done
            )

        done.assert_awaited_once_with("a", False, 0)

    async def test_once_per_plugin_after_every_query(self) -> None:
        """Two queries: the plugin's second run fails, so it is not finished."""
        plugin = _plugin([])
        plugin.search = AsyncMock(
            side_effect=[[_sr("https://a/1")], RuntimeError("boom")]
        )
        done = AsyncMock()
        runner = _runner(_registry({"a": plugin}))

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q", "q2"], 2000, budget=budget, on_plugin_done=done
            )

        done.assert_awaited_once_with("a", False, 0)

    async def test_the_report_carries_the_dropped_count(self) -> None:
        """Two queries, the filter drops one result in each run."""
        plugin = _plugin([_sr("https://a/1"), _sr("https://a/2")])
        done = AsyncMock()
        runner = _runner(
            _registry({"a": plugin}),
            episode_filter_fn=lambda results, _season, _episode: results[1:],
        )

        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q", "q2"], 2000, budget=budget, on_plugin_done=done
            )

        done.assert_awaited_once_with("a", True, 2)

    async def test_skipped_plugins_are_finished(self) -> None:
        """A breaker, the health check or a mirror group skip a plugin on
        purpose: nothing of its is missing."""
        breaker = MagicMock()
        breaker.allow.side_effect = lambda key: not key.startswith("open")
        breaker.is_closed.return_value = True
        plugins = {name: _plugin([]) for name in ("open", "down", "m1", "m2")}
        done = AsyncMock()
        runner = _runner(
            _registry(plugins),
            circuit_breaker=breaker,
            plugin_health=_Health("down"),
            mirror_groups={"m1": "g", "m2": "g"},
        )

        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)
        async with pool.request() as budget:
            await runner.search_with_fallback(
                list(plugins), ["q"], 2000, budget=budget, on_plugin_done=done
            )

        assert sorted(c.args for c in done.await_args_list) == [
            ("down", True, 0),
            ("m1", True, 0),
            ("m2", True, 0),
            ("open", True, 0),
        ]


class TestPageClaim:
    """A plugin's browser pages are claimed as plugin work, due at its end."""

    @staticmethod
    def _claiming(seen: list[PageClaim | None]) -> MagicMock:
        async def _search(_query: str, **_kwargs: object) -> list[SearchResult]:
            seen.append(page_claim.get())
            return []

        plugin = _plugin([])
        plugin.search = AsyncMock(side_effect=_search)
        return plugin

    async def test_due_at_the_plugin_timeout(self) -> None:
        seen: list[PageClaim | None] = []
        runner = _runner(_registry({"a": self._claiming(seen)}), plugin_timeout=5.0)
        before = time.monotonic()

        await _search(runner, ["a"], ["q"])

        claim = seen[0]
        assert claim is not None
        assert claim.kind == "plugin"
        assert before + 5.0 <= claim.due <= time.monotonic() + 5.0
        assert page_claim.get() is None

    async def test_due_at_the_plugin_timeout_past_the_budget_too(self) -> None:
        seen: list[PageClaim | None] = []
        runner = _runner(_registry({"a": self._claiming(seen)}), plugin_timeout=5.0)
        before = time.monotonic()
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=10)

        async with pool.request() as budget:
            await runner.search_with_fallback(
                ["a"], ["q"], 2000, budget=budget, budget_ends=before - 1
            )

        claim = seen[0]
        assert claim is not None
        assert before + 5.0 <= claim.due <= time.monotonic() + 5.0
