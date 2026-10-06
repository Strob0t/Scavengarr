"""Tests for PageBudget: the stealth browser's page count follows the waits
for a page, the CPU and the free memory."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from unittest.mock import MagicMock, patch

from scavengarr.infrastructure.browser.page_budget import PageBudget
from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.resource_detector import ResourceUsage

_MOD = "scavengarr.infrastructure.browser.page_budget"
_MB = 1024**2


def _usage(*, cpu: float | None = 0.5, free_mb: int | None = 4000) -> ResourceUsage:
    return ResourceUsage(
        cpu_busy=cpu, memory_free=None if free_mb is None else free_mb * _MB
    )


def _budget(*, limit: int = 2, ceiling: int = 4) -> tuple[PageBudget, PageGate]:
    gate = PageGate(limit=limit)
    return PageBudget(gate, ceiling=ceiling, sampler=MagicMock()), gate


class TestUp:
    """One page more when a request waited and the machine has room."""

    def test_a_long_wait_adds_a_page(self) -> None:
        budget, gate = _budget()

        budget.step(_usage(), waited=1.5, now=100.0)

        assert gate.limit == 3

    def test_a_short_wait_adds_none(self) -> None:
        budget, gate = _budget()

        budget.step(_usage(), waited=0.5, now=100.0)

        assert gate.limit == 2

    def test_a_cpu_above_70_percent_adds_none(self) -> None:
        budget, gate = _budget()

        budget.step(_usage(cpu=0.8), waited=5.0, now=100.0)

        assert gate.limit == 2

    def test_less_than_two_pages_of_memory_adds_none(self) -> None:
        budget, gate = _budget()

        budget.step(_usage(free_mb=300), waited=5.0, now=100.0)

        assert gate.limit == 2

    def test_unknown_cpu_or_memory_adds_none(self) -> None:
        budget, gate = _budget()

        budget.step(_usage(cpu=None), waited=5.0, now=100.0)
        budget.step(_usage(free_mb=None), waited=5.0, now=200.0)

        assert gate.limit == 2

    def test_the_ceiling_holds(self) -> None:
        budget, gate = _budget(limit=4, ceiling=4)

        budget.step(_usage(), waited=5.0, now=100.0)

        assert gate.limit == 4

    def test_the_next_page_comes_30_s_after_the_last_change(self) -> None:
        """A new page's own CPU shows only after it ran."""
        budget, gate = _budget()
        budget.step(_usage(), waited=5.0, now=100.0)

        budget.step(_usage(), waited=5.0, now=125.0)
        assert gate.limit == 3
        budget.step(_usage(), waited=5.0, now=130.0)
        assert gate.limit == 4


class TestDown:
    """One page less under memory or CPU pressure, never below one."""

    def test_low_memory_takes_a_page_at_once(self) -> None:
        budget, gate = _budget(limit=3)

        budget.step(_usage(free_mb=150), waited=5.0, now=100.0)
        assert gate.limit == 2
        budget.step(_usage(free_mb=150), waited=5.0, now=105.0)
        assert gate.limit == 1

    def test_a_busy_cpu_on_two_samples_in_a_row_takes_a_page(self) -> None:
        budget, gate = _budget(limit=3)

        budget.step(_usage(cpu=0.95), waited=0.0, now=100.0)
        assert gate.limit == 3
        budget.step(_usage(cpu=0.95), waited=0.0, now=105.0)
        assert gate.limit == 2

    def test_one_busy_sample_takes_none(self) -> None:
        budget, gate = _budget(limit=3)

        for cpu, now in ((0.95, 100.0), (0.5, 105.0), (0.95, 110.0)):
            budget.step(_usage(cpu=cpu), waited=0.0, now=now)

        assert gate.limit == 3

    def test_the_band_between_70_and_90_percent_holds(self) -> None:
        budget, gate = _budget(limit=3)

        for now in (100.0, 105.0, 110.0):
            budget.step(_usage(cpu=0.85), waited=5.0, now=now)

        assert gate.limit == 3

    def test_the_cpu_takes_the_next_page_10_s_after_the_last_change(self) -> None:
        budget, gate = _budget(limit=4)
        budget.step(_usage(cpu=0.95), waited=0.0, now=100.0)
        budget.step(_usage(cpu=0.95), waited=0.0, now=105.0)

        budget.step(_usage(cpu=0.95), waited=0.0, now=110.0)
        assert gate.limit == 3
        budget.step(_usage(cpu=0.95), waited=0.0, now=115.0)
        assert gate.limit == 2

    def test_one_page_stays(self) -> None:
        budget, gate = _budget(limit=1)

        for now in (100.0, 105.0, 120.0):
            budget.step(_usage(cpu=0.95, free_mb=50), waited=5.0, now=now)

        assert gate.limit == 1


class TestIdle:
    """Two minutes without waits and changes bring the start value back."""

    def test_a_burst_leaves_no_large_limit_behind(self) -> None:
        budget, gate = _budget()
        budget.step(_usage(), waited=5.0, now=100.0)
        budget.step(_usage(), waited=5.0, now=130.0)

        budget.step(_usage(), waited=0.0, now=245.0)
        assert gate.limit == 4
        budget.step(_usage(), waited=0.0, now=250.0)
        assert gate.limit == 2

    def test_short_waits_keep_the_pages(self) -> None:
        budget, gate = _budget()
        budget.step(_usage(), waited=5.0, now=100.0)
        budget.step(_usage(), waited=0.2, now=200.0)

        budget.step(_usage(), waited=0.0, now=315.0)
        assert gate.limit == 3
        budget.step(_usage(), waited=0.0, now=320.0)
        assert gate.limit == 2

    def test_after_cpu_pressure_the_start_value_comes_back(self) -> None:
        budget, gate = _budget()
        budget.step(_usage(cpu=0.95), waited=0.0, now=100.0)
        budget.step(_usage(cpu=0.95), waited=0.0, now=105.0)
        assert gate.limit == 1

        budget.step(_usage(), waited=0.0, now=220.0)
        assert gate.limit == 1
        budget.step(_usage(), waited=0.0, now=225.0)
        assert gate.limit == 2

    def test_the_start_value_comes_back_only_with_room(self) -> None:
        budget, gate = _budget()
        budget.step(_usage(free_mb=150), waited=0.0, now=100.0)

        budget.step(_usage(cpu=0.8), waited=0.0, now=225.0)
        budget.step(_usage(cpu=None), waited=0.0, now=230.0)

        assert gate.limit == 1


class TestRun:
    async def test_every_tick_samples_and_adapts(self) -> None:
        gate = PageGate(limit=2)
        sampler = MagicMock()
        sampler.sample.return_value = _usage(free_mb=100)
        budget = PageBudget(gate, ceiling=4, sampler=sampler)

        with patch(f"{_MOD}._TICK_S", 0.01):
            task = asyncio.create_task(budget.run_forever())
            await asyncio.sleep(0.05)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        assert gate.limit == 1
        assert sampler.sample.call_count >= 2
