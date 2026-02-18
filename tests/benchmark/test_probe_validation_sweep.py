"""Probe and validation semaphore sweep benchmark.

Simulates the ``asyncio.Semaphore(N) + asyncio.gather()`` pattern used
by hoster probes and link validation to find diminishing-returns
thresholds for concurrency caps.

Run manually::

    poetry run pytest tests/benchmark/test_probe_validation_sweep.py -s -v
"""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from .conftest import BenchmarkResult, format_table

# ---------------------------------------------------------------------------
# Simulated I/O tasks
# ---------------------------------------------------------------------------


async def _probe_one(latency: float) -> bool:
    """Simulate a single HEAD probe request."""
    await asyncio.sleep(latency)
    return True


async def _validate_one(head_latency: float, *, get_fallback_rate: float = 0.2) -> bool:
    """Simulate a link validation (HEAD + optional GET fallback)."""
    await asyncio.sleep(head_latency)
    if random.random() < get_fallback_rate:
        # GET fallback takes longer
        await asyncio.sleep(head_latency * 2.5)
    return True


async def _run_semaphore_sweep(
    *,
    task_fn,
    concurrency: int,
    url_count: int,
    label: str,
) -> BenchmarkResult:
    """Run a batch of tasks through a semaphore and measure timing."""
    sem = asyncio.Semaphore(concurrency)

    async def _wrapped(idx: int) -> int:
        async with sem:
            await task_fn()
            return idx

    t0 = time.perf_counter_ns()
    tasks = [_wrapped(i) for i in range(url_count)]
    await asyncio.gather(*tasks)
    wall_ns = time.perf_counter_ns() - t0

    return BenchmarkResult(
        scenario=f"{label}:c={concurrency},n={url_count}",
        httpx_slots=concurrency,
        pw_slots=0,
        plugin_count=0,
        concurrent_requests=url_count,
        total_wall_ns=wall_ns,
        per_request_latencies_ns=[wall_ns],
        completed=url_count,
        failed=0,
    )


# ---------------------------------------------------------------------------
# Probe sweep
# ---------------------------------------------------------------------------

_PROBE_CONCURRENCY = [4, 8, 16, 32, 48, 64, 96, 128, 160, 200]
_PROBE_URL_COUNTS = [50, 100, 200, 500]
_PROBE_LATENCY = 0.01  # 10ms simulated HEAD probe


@pytest.mark.benchmark
class TestProbeSweep:
    """Sweep probe semaphore size to find diminishing-returns threshold."""

    @pytest.mark.asyncio
    async def test_probe_concurrency_sweep(self) -> None:
        results: list[BenchmarkResult] = []

        for url_count in _PROBE_URL_COUNTS:
            for conc in _PROBE_CONCURRENCY:
                result = await _run_semaphore_sweep(
                    task_fn=lambda: _probe_one(_PROBE_LATENCY),
                    concurrency=conc,
                    url_count=url_count,
                    label="probe",
                )
                results.append(result)

        # Print results
        headers = ["URLs", "Concurrency", "Wall(ms)", "Throughput(req/s)"]
        rows = [
            [
                r.concurrent_requests,
                r.httpx_slots,
                f"{r.wall_ms:.0f}",
                f"{r.throughput:.0f}",
            ]
            for r in results
        ]
        print(format_table(headers, rows, title="PROBE CONCURRENCY SWEEP"))

        # Find diminishing-returns threshold per URL count
        self._print_diminishing_returns(results, "PROBE")

        assert all(r.failed == 0 for r in results)

    def _print_diminishing_returns(
        self,
        results: list[BenchmarkResult],
        label: str,
    ) -> None:
        """Find where throughput gains drop below 5%."""
        by_url_count: dict[int, list[BenchmarkResult]] = {}
        for r in results:
            by_url_count.setdefault(r.concurrent_requests, []).append(r)

        headers = ["URLs", "Threshold Conc", "Throughput", "vs Previous"]
        rows = []
        for url_count, group in sorted(by_url_count.items()):
            group.sort(key=lambda r: r.httpx_slots)
            threshold_conc = group[-1].httpx_slots
            threshold_tp = group[-1].throughput

            for i in range(1, len(group)):
                prev_tp = group[i - 1].throughput
                curr_tp = group[i].throughput
                if prev_tp > 0:
                    gain = (curr_tp - prev_tp) / prev_tp
                    if gain < 0.05:  # <5% improvement
                        threshold_conc = group[i - 1].httpx_slots
                        threshold_tp = prev_tp
                        break

            rows.append(
                [
                    url_count,
                    threshold_conc,
                    f"{threshold_tp:.0f}",
                    "<5% gain after this",
                ]
            )

        print(format_table(headers, rows, title=f"{label} DIMINISHING RETURNS"))


# ---------------------------------------------------------------------------
# Validation sweep
# ---------------------------------------------------------------------------

_VALIDATION_CONCURRENCY = [5, 10, 20, 40, 60, 80, 100, 150, 200]
_VALIDATION_LINK_COUNTS = [20, 50, 100, 200, 500]
_VALIDATION_HEAD_LATENCY = 0.008  # 8ms simulated HEAD


@pytest.mark.benchmark
class TestValidationSweep:
    """Sweep validation semaphore size to find optimal concurrency."""

    @pytest.mark.asyncio
    async def test_validation_concurrency_sweep(self) -> None:
        results: list[BenchmarkResult] = []

        for link_count in _VALIDATION_LINK_COUNTS:
            for conc in _VALIDATION_CONCURRENCY:
                result = await _run_semaphore_sweep(
                    task_fn=lambda: _validate_one(_VALIDATION_HEAD_LATENCY),
                    concurrency=conc,
                    url_count=link_count,
                    label="validation",
                )
                results.append(result)

        headers = ["Links", "Concurrency", "Wall(ms)", "Throughput(req/s)"]
        rows = [
            [
                r.concurrent_requests,
                r.httpx_slots,
                f"{r.wall_ms:.0f}",
                f"{r.throughput:.0f}",
            ]
            for r in results
        ]
        print(format_table(headers, rows, title="VALIDATION CONCURRENCY SWEEP"))

        # Find diminishing returns
        by_link_count: dict[int, list[BenchmarkResult]] = {}
        for r in results:
            by_link_count.setdefault(r.concurrent_requests, []).append(r)

        headers2 = ["Links", "Threshold Conc", "Throughput", "Note"]
        rows2 = []
        for link_count, group in sorted(by_link_count.items()):
            group.sort(key=lambda r: r.httpx_slots)
            threshold_conc = group[-1].httpx_slots
            threshold_tp = group[-1].throughput

            for i in range(1, len(group)):
                prev_tp = group[i - 1].throughput
                curr_tp = group[i].throughput
                if prev_tp > 0:
                    gain = (curr_tp - prev_tp) / prev_tp
                    if gain < 0.05:
                        threshold_conc = group[i - 1].httpx_slots
                        threshold_tp = prev_tp
                        break

            rows2.append(
                [
                    link_count,
                    threshold_conc,
                    f"{threshold_tp:.0f}",
                    "<5% gain",
                ]
            )

        print(format_table(headers2, rows2, title="VALIDATION DIMINISHING RETURNS"))

        assert all(r.failed == 0 for r in results)
