"""Tests for PluginSearchRunner (plugin fan-out for Stremio requests)."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.stremio.plugin_search import PluginSearchRunner
from scavengarr.domain.plugins.base import SearchResult
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
        breaker.record_failure.assert_called_once_with("a")
        breaker.record_success.assert_not_called()
        assert metrics.record_plugin_search.call_args.kwargs == {"success": False}

    async def test_success_recorded(self) -> None:
        breaker = MagicMock()
        breaker.allow.return_value = True
        registry = _registry({"a": _plugin([_sr("https://a/1")])})

        await _search(_runner(registry, circuit_breaker=breaker), ["a"], ["q"])

        breaker.record_success.assert_called_once_with("a")

    async def test_unknown_plugin_returns_empty(self) -> None:
        registry = MagicMock()
        registry.get.side_effect = KeyError("missing")
        registry.get_mode.return_value = "httpx"

        assert await _search(_runner(registry), ["missing"], ["q"]) == []


class TestCircuitBreakerAndTimeout:
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
        breaker.record_failure.assert_called_once_with("a")


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
