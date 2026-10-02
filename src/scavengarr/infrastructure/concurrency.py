"""Global concurrency pool with fair-share budgets per request.

Provides two slot pools (httpx + Playwright) shared across all
concurrent Stremio requests.  Each request receives a ``RequestBudget``
that dynamically limits how many slots it may hold based on the
number of active requests.

Fair-share algorithm:
    fair_share = max(1, total_slots // active_requests)

When a request exits, remaining requests automatically see a
larger fair-share allowance.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog

log = structlog.get_logger(__name__)

_SlotKind = Literal["httpx", "pw"]


class RequestBudget:
    """Per-request concurrency budget enforcing fair-share limits.

    Created by :meth:`ConcurrencyPool.request` — not instantiated
    directly.  Tracks how many global slots this request currently
    holds and blocks when the fair-share limit is reached.
    """

    def __init__(
        self,
        *,
        httpx_sem: asyncio.Semaphore,
        pw_sem: asyncio.Semaphore,
        pool: ConcurrencyPool,
        condition: asyncio.Condition,
    ) -> None:
        self._httpx_sem = httpx_sem
        self._pw_sem = pw_sem
        self._pool = pool
        self._condition = condition
        # Slots this request holds or has reserved, per kind
        self._held: dict[_SlotKind, int] = {"httpx": 0, "pw": 0}

    def _fair_share(self, slots: int) -> int:
        active = self._pool.active_requests
        return max(1, slots // active) if active > 0 else 1

    def _httpx_fair_share(self) -> int:
        return self._fair_share(self._pool.httpx_slots)

    def _pw_fair_share(self) -> int:
        return self._fair_share(self._pool.pw_slots)

    @asynccontextmanager
    async def acquire_httpx(self) -> AsyncIterator[None]:
        """Acquire one httpx slot, respecting fair-share budget."""
        async with self._slot("httpx"):
            yield

    @asynccontextmanager
    async def acquire_pw(self) -> AsyncIterator[None]:
        """Acquire one Playwright slot, respecting fair-share budget."""
        async with self._slot("pw"):
            yield

    @asynccontextmanager
    async def _slot(self, kind: _SlotKind) -> AsyncIterator[None]:
        if kind == "httpx":
            sem, fair_share = self._httpx_sem, self._httpx_fair_share
        else:
            sem, fair_share = self._pw_sem, self._pw_fair_share
        # Reserve under the condition, before waiting for a global slot:
        # counting only after the semaphore let every waiting task of one
        # request pass the fair-share check at once
        async with self._condition:
            while self._held[kind] >= fair_share():
                await self._condition.wait()
            self._held[kind] += 1
        try:
            await sem.acquire()
        except BaseException:
            await self._unreserve(kind)
            raise
        try:
            yield
        finally:
            sem.release()
            await self._unreserve(kind)

    async def _unreserve(self, kind: _SlotKind) -> None:
        async with self._condition:
            self._held[kind] -= 1
            self._condition.notify_all()


class ConcurrencyPool:
    """Application-level singleton managing global concurrency slots.

    Holds two :class:`asyncio.Semaphore` objects (httpx + Playwright)
    and an active request counter.  Each call to :meth:`request`
    returns a :class:`RequestBudget` scoped to that request.

    Parameters:
        httpx_slots: Total httpx concurrency slots (shared globally).
        pw_slots: Total Playwright concurrency slots (shared globally).
    """

    def __init__(self, *, httpx_slots: int = 10, pw_slots: int = 3) -> None:
        self.httpx_slots = httpx_slots
        self.pw_slots = pw_slots
        self._httpx_sem = asyncio.Semaphore(httpx_slots)
        self._pw_sem = asyncio.Semaphore(pw_slots)
        self._active_requests = 0
        self._condition = asyncio.Condition()

    @property
    def active_requests(self) -> int:
        return self._active_requests

    @asynccontextmanager
    async def request(self) -> AsyncIterator[RequestBudget]:
        """Enter a request scope, returning a fair-share budget.

        Increments the active request counter on entry and
        decrements on exit.  Other requests' fair-share is
        recalculated via :class:`asyncio.Condition` notification.
        """
        async with self._condition:
            self._active_requests += 1
            self._condition.notify_all()
        budget = RequestBudget(
            httpx_sem=self._httpx_sem,
            pw_sem=self._pw_sem,
            pool=self,
            condition=self._condition,
        )
        try:
            yield budget
        finally:
            async with self._condition:
                self._active_requests -= 1
                self._condition.notify_all()
            log.debug(
                "request_budget_released",
                active_requests=self._active_requests,
            )

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot for the /metrics endpoint."""
        return {
            "httpx_slots": self.httpx_slots,
            "pw_slots": self.pw_slots,
            "active_requests": self._active_requests,
            "httpx_available": self._httpx_sem._value,
            "pw_available": self._pw_sem._value,
        }
