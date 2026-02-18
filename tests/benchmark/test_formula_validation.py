"""Auto-tune formula validation benchmark.

Compares current ``_auto_tune()`` formula outputs against empirically
measured optimal values for various resource levels.

Run manually:  ``poetry run pytest tests/benchmark/test_formula_validation.py -s -v``
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from unittest.mock import patch

import pytest

from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.infrastructure.resource_detector import DetectedResources
from scavengarr.interfaces.composition import _auto_tune

from .conftest import (
    BenchmarkResult,
    FakePluginRegistry,
    FakeStremioConfig,
    format_table,
    make_fake_search_engine,
    make_fake_tmdb,
    make_mixed_plugins,
)

# ---------------------------------------------------------------------------
# Resource levels to test
# ---------------------------------------------------------------------------

_RESOURCE_LEVELS: list[tuple[str, int, float]] = [
    # (label, cpu_cores, ram_gb)
    ("1cpu/2GB", 1, 2.0),
    ("2cpu/4GB", 2, 4.0),
    ("4cpu/8GB", 4, 8.0),
    ("8cpu/16GB", 8, 16.0),
    ("16cpu/32GB", 16, 32.0),
]

_HTTPX_SLOT_RANGE = list(range(2, 31, 2))  # [2, 4, 6, ..., 30]

_max_results_var: ContextVar[int | None] = ContextVar(
    "formula_bench_max_results", default=None
)


def _noop_filter(results, *_args, **_kwargs):
    return results


def _noop_convert(results, **_kwargs):
    return []


# ---------------------------------------------------------------------------
# Empirical sweep helper
# ---------------------------------------------------------------------------


async def _measure_wall_time(
    *,
    httpx_slots: int,
    pw_slots: int,
    plugin_count: int = 20,
) -> float:
    """Measure wall time for a single search with given slot config.

    Returns wall time in milliseconds.
    """
    from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase

    plugins = make_mixed_plugins(plugin_count, fast_latency=0.05, slow_latency=0.15)
    pool = ConcurrencyPool(httpx_slots=httpx_slots, pw_slots=pw_slots)
    registry = FakePluginRegistry(plugins)
    config = FakeStremioConfig(
        max_concurrent_plugins=httpx_slots,
        max_concurrent_playwright=pw_slots,
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
    t0 = time.perf_counter_ns()
    async with pool.request() as budget:
        await use_case._search_plugins(
            plugin_names, "bench", 2000, budget=budget
        )
    wall_ns = time.perf_counter_ns() - t0
    return wall_ns / 1_000_000


# ---------------------------------------------------------------------------
# Formula comparison benchmark
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestFormulaValidation:
    """Compare _auto_tune() output vs. empirical optimum."""

    @pytest.mark.asyncio
    async def test_formula_vs_empirical(self) -> None:
        """Sweep httpx_slots per resource level, compare with formula."""
        headers = [
            "Level",
            "CPU",
            "RAM(GB)",
            "Formula",
            "Empirical",
            "Delta",
            "Formula(ms)",
            "Best(ms)",
        ]
        rows = []

        for label, cpus, ram_gb in _RESOURCE_LEVELS:
            # Get formula output
            resources = DetectedResources(
                cpu_cores=cpus,
                memory_bytes=int(ram_gb * 1024**3),
                cpu_source="cgroup_v2",
                mem_source="cgroup_v2",
                cgroup_limited=True,
            )
            config = AppConfig()
            with patch(
                "scavengarr.interfaces.composition.detect_resources",
                return_value=resources,
            ):
                _auto_tune(config)

            formula_slots = config.stremio.max_concurrent_plugins

            # Empirical sweep
            best_slots = formula_slots
            best_wall = float("inf")

            for slots in _HTTPX_SLOT_RANGE:
                pw_slots = max(1, min(cpus, 10))
                wall = await _measure_wall_time(
                    httpx_slots=slots,
                    pw_slots=pw_slots,
                )
                if wall < best_wall:
                    best_wall = wall
                    best_slots = slots

            # Measure formula slots too
            formula_wall = await _measure_wall_time(
                httpx_slots=formula_slots,
                pw_slots=max(1, min(cpus, 10)),
            )

            delta = formula_slots - best_slots
            rows.append([
                label,
                cpus,
                f"{ram_gb:.0f}",
                formula_slots,
                best_slots,
                f"{delta:+d}",
                f"{formula_wall:.0f}",
                f"{best_wall:.0f}",
            ])

        print(format_table(headers, rows, title="FORMULA vs EMPIRICAL OPTIMAL"))

        # No assertion on optimality — this is exploratory.
        # The table output is the value.

    def test_formula_monotonic_scaling(self) -> None:
        """Auto-tune values should increase monotonically with resources."""
        prev_plugins = 0
        prev_pw = 0
        prev_probe = 0
        prev_validation = 0

        for _, cpus, ram_gb in _RESOURCE_LEVELS:
            resources = DetectedResources(
                cpu_cores=cpus,
                memory_bytes=int(ram_gb * 1024**3),
                cpu_source="cgroup_v2",
                mem_source="cgroup_v2",
                cgroup_limited=True,
            )
            config = AppConfig()
            with patch(
                "scavengarr.interfaces.composition.detect_resources",
                return_value=resources,
            ):
                _auto_tune(config)

            assert config.stremio.max_concurrent_plugins >= prev_plugins
            assert config.stremio.max_concurrent_playwright >= prev_pw
            assert config.stremio.probe_concurrency >= prev_probe
            assert config.validation_max_concurrent >= prev_validation

            prev_plugins = config.stremio.max_concurrent_plugins
            prev_pw = config.stremio.max_concurrent_playwright
            prev_probe = config.stremio.probe_concurrency
            prev_validation = config.validation_max_concurrent

    def test_formula_bounds(self) -> None:
        """All formula outputs stay within documented min/max ranges."""
        for _, cpus, ram_gb in _RESOURCE_LEVELS:
            resources = DetectedResources(
                cpu_cores=cpus,
                memory_bytes=int(ram_gb * 1024**3),
                cpu_source="cgroup_v2",
                mem_source="cgroup_v2",
                cgroup_limited=True,
            )
            config = AppConfig()
            with patch(
                "scavengarr.interfaces.composition.detect_resources",
                return_value=resources,
            ):
                _auto_tune(config)

            s = config.stremio
            # max_concurrent_plugins: min 2, max 30
            assert 2 <= s.max_concurrent_plugins <= 30
            # max_concurrent_playwright: min 1, max 10
            assert 1 <= s.max_concurrent_playwright <= 10
            # probe_concurrency: min 4, no current cap (this test documents it)
            assert s.probe_concurrency >= 4
            # validation_max_concurrent: min 5, no current cap
            assert config.validation_max_concurrent >= 5

    def test_formula_probe_validation_capped_on_large_hosts(self) -> None:
        """Verify probe/validation are hard-capped on large hosts."""
        resources = DetectedResources(
            cpu_cores=32,
            memory_bytes=64 * 1024**3,
            cpu_source="os_fallback",
            mem_source="os_fallback",
            cgroup_limited=False,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        print(
            f"\n32-core host: probe={config.stremio.probe_concurrency}, "
            f"validation={config.validation_max_concurrent}"
        )
        # Caps derived from benchmark diminishing-returns analysis
        assert config.stremio.probe_concurrency == 100  # min(32*4, 100)
        assert config.validation_max_concurrent == 120  # min(32*5, 120)
