"""Plugin concurrency slot sweep benchmark.

Varies ``httpx_slots`` for different plugin counts and simultaneous
requests to find optimal ConcurrencyPool configuration.

Run manually:  ``poetry run pytest tests/benchmark/test_concurrency_tuning.py -s -v``
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar

import pytest

from scavengarr.infrastructure.concurrency import ConcurrencyPool

from .conftest import (
    BenchmarkResult,
    FakePluginRegistry,
    FakeStremioConfig,
    LatencyPlugin,
    format_table,
    make_fake_search_engine,
    make_fake_tmdb,
    make_mixed_plugins,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_max_results_var: ContextVar[int | None] = ContextVar("bench_max_results", default=None)


def _noop_filter(results, *_args, **_kwargs):
    """Title filter that accepts everything."""
    return results


def _noop_convert(results, **_kwargs):
    """Convert function that returns empty (we only measure scheduling)."""
    return []


async def _run_scenario(
    *,
    plugins: list[LatencyPlugin],
    httpx_slots: int,
    pw_slots: int,
    concurrent_requests: int,
    plugin_timeout: float = 30.0,
) -> BenchmarkResult:
    """Run a single benchmark scenario and return timing results.

    Creates a ConcurrencyPool, builds a minimal StremioStreamUseCase
    (bypassing TMDB/convert/sort — only _search_plugins is exercised),
    and fires ``concurrent_requests`` parallel search calls.
    """
    from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase

    pool = ConcurrencyPool(httpx_slots=httpx_slots, pw_slots=pw_slots)
    registry = FakePluginRegistry(plugins)
    config = FakeStremioConfig(
        max_concurrent_plugins=httpx_slots,
        max_concurrent_playwright=pw_slots,
        plugin_timeout_seconds=plugin_timeout,
    )

    use_case = StremioStreamUseCase(
        tmdb=make_fake_tmdb(),
        plugins=registry,
        search_engine=make_fake_search_engine(),
        config=config,
        sorter=type("S", (), {"sort": staticmethod(lambda x: x)})(),
        convert_fn=_noop_convert,
        filter_fn=_noop_filter,
        user_agent="benchmark/1.0",
        max_results_var=_max_results_var,
        pool=pool,
    )

    plugin_names = registry.list_names()
    latencies_ns: list[int] = []
    failed = 0

    async def _one_request() -> None:
        nonlocal failed
        t0 = time.perf_counter_ns()
        try:
            async with pool.request() as budget:
                await use_case._search_plugins(
                    plugin_names,
                    "benchmark query",
                    2000,
                    budget=budget,
                )
        except Exception:
            failed += 1
        finally:
            latencies_ns.append(time.perf_counter_ns() - t0)

    wall_t0 = time.perf_counter_ns()
    await asyncio.gather(*[_one_request() for _ in range(concurrent_requests)])
    wall_ns = time.perf_counter_ns() - wall_t0

    return BenchmarkResult(
        scenario=f"p={len(plugins)},h={httpx_slots},pw={pw_slots},r={concurrent_requests}",
        httpx_slots=httpx_slots,
        pw_slots=pw_slots,
        plugin_count=len(plugins),
        concurrent_requests=concurrent_requests,
        total_wall_ns=wall_ns,
        per_request_latencies_ns=latencies_ns,
        completed=concurrent_requests - failed,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# Main slot sweep
# ---------------------------------------------------------------------------

_PLUGIN_COUNTS = [5, 10, 20, 42]
_SLOT_COUNTS = [2, 4, 8, 12, 16, 20, 30, 50]
_CONCURRENT_REQUESTS = [1, 2, 4, 8]


@pytest.mark.benchmark
class TestHttpxSlotSweep:
    """Sweep httpx_slots across plugin counts and concurrent requests."""

    @pytest.mark.asyncio
    async def test_httpx_slot_sweep(self) -> None:
        """Main benchmark: find optimal httpx_slots per scenario."""
        results: list[BenchmarkResult] = []

        for n_plugins in _PLUGIN_COUNTS:
            plugins = make_mixed_plugins(
                n_plugins, fast_latency=0.05, slow_latency=0.15
            )
            n_pw = sum(1 for p in plugins if p.mode == "playwright")

            for n_requests in _CONCURRENT_REQUESTS:
                for slots in _SLOT_COUNTS:
                    pw_slots = max(1, min(n_pw, 10))
                    result = await _run_scenario(
                        plugins=plugins,
                        httpx_slots=slots,
                        pw_slots=pw_slots,
                        concurrent_requests=n_requests,
                    )
                    results.append(result)

        # --- Print results table ---
        headers = [
            "Plugins",
            "Reqs",
            "Slots",
            "Wall(ms)",
            "p50(ms)",
            "p95(ms)",
            "Throughput",
        ]
        rows = [
            [
                r.plugin_count,
                r.concurrent_requests,
                r.httpx_slots,
                f"{r.wall_ms:.0f}",
                f"{r.p50_ms:.0f}",
                f"{r.p95_ms:.0f}",
                f"{r.throughput:.1f}",
            ]
            for r in results
        ]
        print(format_table(headers, rows, title="HTTPX Slot Sweep"))

        # --- Find optimal slots per (plugin_count, concurrent_requests) ---
        best: dict[tuple[int, int], BenchmarkResult] = {}
        for r in results:
            key = (r.plugin_count, r.concurrent_requests)
            if key not in best or r.wall_ms < best[key].wall_ms:
                best[key] = r

        print(
            format_table(
                ["Plugins", "Reqs", "Best Slots", "Wall(ms)", "Throughput"],
                [
                    [
                        k[0],
                        k[1],
                        v.httpx_slots,
                        f"{v.wall_ms:.0f}",
                        f"{v.throughput:.1f}",
                    ]
                    for k, v in sorted(best.items())
                ],
                title="OPTIMAL HTTPX SLOTS",
            )
        )

        # Verify benchmarks actually ran
        assert len(results) > 0
        assert all(r.failed == 0 for r in results)

    @pytest.mark.asyncio
    async def test_fair_share_under_contention(self) -> None:
        """8 concurrent requests, 20 plugins, 8 slots — p99 < 5x p50."""
        plugins = make_mixed_plugins(20, fast_latency=0.05, slow_latency=0.15)
        n_pw = sum(1 for p in plugins if p.mode == "playwright")

        result = await _run_scenario(
            plugins=plugins,
            httpx_slots=8,
            pw_slots=max(1, n_pw),
            concurrent_requests=8,
        )

        print(
            f"\nFair-share contention: wall={result.wall_ms:.0f}ms "
            f"p50={result.p50_ms:.0f}ms p99={result.p99_ms:.0f}ms"
        )

        assert result.failed == 0
        # Under fair-share, p99 should not be wildly worse than p50
        if result.p50_ms > 0:
            ratio = result.p99_ms / result.p50_ms
            print(f"  p99/p50 ratio: {ratio:.2f}")
            assert ratio < 5.0, f"p99/p50 ratio {ratio:.2f} exceeds 5x"

    @pytest.mark.asyncio
    async def test_timeout_edge_with_slow_plugins(self) -> None:
        """10 fast + 5 very slow plugins, timeout=2s — wall < 3s."""
        fast = [
            LatencyPlugin(name=f"fast_{i}", latency_seconds=0.05, mode="httpx")
            for i in range(10)
        ]
        slow = [
            LatencyPlugin(name=f"slow_{i}", latency_seconds=5.0, mode="httpx")
            for i in range(5)
        ]
        plugins = fast + slow

        result = await _run_scenario(
            plugins=plugins,
            httpx_slots=10,
            pw_slots=1,
            concurrent_requests=1,
            plugin_timeout=2.0,
        )

        print(f"\nTimeout edge: wall={result.wall_ms:.0f}ms")

        # Slow plugins should be timed out, wall should be bounded
        assert result.wall_ms < 3000, f"Wall time {result.wall_ms:.0f}ms exceeds 3s"
