"""The stealth browser's pages: how many may be open, and who gets the next.

Plugin pages behind Cloudflare, hoster captures and link-outs share one
headful browser, and a page costs about 200 MB and much CPU. Pages are
handed out in the order they were asked for. The work's claim
(``page_claim``) names what a page is for; the wait for a page and the work
on it are recorded by that kind (``browser_page_wait``, ``browser_page``).
See ``docs/plans/browser-page-budget.md``.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from scavengarr.domain.ports.browser_fetcher import PageClaim, PageKind, page_claim
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort


@dataclass(order=True)
class _Waiter:
    """A page request that waits; the smallest key is served first."""

    key: tuple[int, ...]
    granted: asyncio.Future[None] = field(compare=False)


class PageGate:
    """At most *limit* pages at a time, handed out in arrival order."""

    def __init__(self, *, limit: int, telemetry: TelemetryPort = NO_TELEMETRY) -> None:
        self._limit = limit
        self._telemetry = telemetry
        self._in_use = 0
        self._waiters: list[_Waiter] = []
        self._arrivals = itertools.count()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    @asynccontextmanager
    async def page(self, kind: PageKind, *, timeout: float) -> AsyncIterator[None]:
        """Hold one page for the work of the current claim.

        Without a claim the work counts as *kind*, due after its own
        *timeout*.
        """
        claim = page_claim.get() or PageClaim(kind, time.monotonic() + timeout)
        with self._telemetry.stage("browser_page_wait", kind=claim.kind):
            await self._acquire()
        try:
            with self._telemetry.stage("browser_page", kind=claim.kind):
                yield
        finally:
            self._release()

    async def _acquire(self) -> None:
        if self._in_use < self._limit and not self._waiters:
            self._in_use += 1
            return
        waiter = _Waiter(
            (next(self._arrivals),), asyncio.get_running_loop().create_future()
        )
        self._waiters.append(waiter)
        try:
            await waiter.granted
        except asyncio.CancelledError:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            elif not waiter.granted.cancelled():
                # Granted, but cut before it ran: the page goes on
                self._release()
            raise

    def _release(self) -> None:
        self._in_use -= 1
        self._grant()

    def _grant(self) -> None:
        """Hand free pages to the waiters, first key first."""
        while self._waiters and self._in_use < self._limit:
            waiter = min(self._waiters)
            self._waiters.remove(waiter)
            self._in_use += 1
            waiter.granted.set_result(None)
