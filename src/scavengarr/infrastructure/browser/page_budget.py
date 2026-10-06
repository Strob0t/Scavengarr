"""How many stealth browser pages may run at once, adapted every few seconds.

The gate's limit starts at ``min(2, ceiling)``; the ceiling is
``stremio.max_concurrent_playwright``, auto-tuned from the container's CPUs
and memory. One page more when a page request waited and the machine has
room, one page less under memory or CPU pressure, the start value back
after two quiet minutes. A lower limit takes no running page away. See
``docs/plans/browser-page-budget.md``.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Literal

import structlog

from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.resource_detector import ResourceSampler, ResourceUsage

log = structlog.get_logger(__name__)

_TICK_S = 5.0

# CPU busy share: at most _CPU_LOW for a page more, at least _CPU_HIGH on
# two samples in a row for a page less; the band between holds the count
_CPU_LOW = 0.70
_CPU_HIGH = 0.90

# What one page costs: Chromium's renderer with a hoster's player
_PAGE_BYTES = 200 * 1024**2

# A page request that waited this long asks for a page more
_WAIT_UP_S = 1.0

# The next change waits for the last one to show: a new page's own CPU
# shows only after it ran
_HOLD_UP_S = 30.0
_HOLD_DOWN_S = 10.0

# Without waits and changes this long the start value comes back, so an
# old burst leaves no large limit behind
_IDLE_S = 120.0

_MB = 1024**2

ChangeReason = Literal["wait", "memory", "cpu", "idle"]


class PageBudget:
    """Adapts the gate's limit to the waits for a page, the CPU and the free
    memory, between one page and *ceiling*."""

    def __init__(
        self,
        gate: PageGate,
        *,
        ceiling: int,
        sampler: ResourceSampler | None = None,
    ) -> None:
        self._gate = gate
        self._start = gate.limit
        self._ceiling = ceiling
        self._sampler = sampler or ResourceSampler()
        self._changed = -math.inf  # when the limit last changed
        self._waited = -math.inf  # when a page request last waited
        self._cpu_was_high = False

    async def run_forever(self) -> None:
        """Adapt the limit every ``_TICK_S`` seconds, until cancelled."""
        while True:
            await asyncio.sleep(_TICK_S)
            self.step(
                self._sampler.sample(),
                waited=self._gate.take_longest_wait(),
                now=time.monotonic(),
            )

    def step(self, usage: ResourceUsage, *, waited: float, now: float) -> None:
        """Apply the first rule that holds for one sample.

        *waited* is the longest wait for a page since the last step.
        """
        limit = self._gate.limit
        cpu, free = usage.cpu_busy, usage.memory_free
        cpu_high = cpu is not None and cpu >= _CPU_HIGH
        cpu_high_twice = cpu_high and self._cpu_was_high
        self._cpu_was_high = cpu_high
        if waited > 0:
            self._waited = now
        roomy = (
            cpu is not None
            and cpu <= _CPU_LOW
            and free is not None
            and free >= 2 * _PAGE_BYTES
        )
        since = now - self._changed

        reason: ChangeReason
        if limit > 1 and free is not None and free < _PAGE_BYTES:
            new, reason = limit - 1, "memory"
        elif limit > 1 and cpu_high_twice and since >= _HOLD_DOWN_S:
            new, reason = limit - 1, "cpu"
        elif (
            limit < self._ceiling
            and waited >= _WAIT_UP_S
            and roomy
            and since >= _HOLD_UP_S
        ):
            new, reason = limit + 1, "wait"
        elif (
            limit != self._start
            and now - max(self._waited, self._changed) >= _IDLE_S
            and (limit > self._start or roomy)
        ):
            new, reason = self._start, "idle"
        else:
            return

        self._gate.set_limit(new)
        self._changed = now
        log.info(
            "browser_pages_changed",
            pages=new,
            previous=limit,
            reason=reason,
            cpu_busy=None if cpu is None else round(cpu, 2),
            memory_free_mb=None if free is None else free // _MB,
            waited_s=round(waited, 1),
        )
