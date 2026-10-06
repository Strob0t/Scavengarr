"""The stealth browser's pages: how many may be open, and who gets the next.

Plugin pages behind Cloudflare, hoster captures and link-outs share one
headful browser, and a page costs about 200 MB and much CPU. The work's
claim (``page_claim``) names what a page is for and when the work is due.
The next free page goes to playback, then to the earliest due work (a
search's plugin pages before the captures of its answer), and to
background work only while nobody else waits; a running page is never
taken away. Work that cannot start ``_MIN_WORK_S`` before it is due gets
no page (``PageBusy``), and the timeout of a resolution (``work_clock``)
stops while it waits. The wait for a page and the work on it are recorded
by kind (``browser_page_wait``, ``browser_page``). ``PageBudget`` changes
the limit while pages are in use. See ``docs/plans/browser-page-budget.md``.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from scavengarr.domain.ports.browser_fetcher import PageClaim, PageKind, page_claim
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort

# Who goes first: playback, then plugin pages and captures by due time,
# background work last
_RANK: dict[PageKind, int] = {"play": 0, "plugin": 1, "capture": 1, "background": 2}

# Work that gets its page later than this before it is due rarely finishes:
# Filemoon's capture, the shortest browser work, took 1.5-2 s (2026-10-06)
_MIN_WORK_S = 3.0

# The timeout of the work that asks for a page (the registry's resolve
# timeout): it measures the work, so it stops while the work waits
work_clock: ContextVar[asyncio.Timeout | None] = ContextVar("work_clock", default=None)


class PageBusy(Exception):
    """No page came free in time for the work's claim."""


@dataclass(order=True)
class _Waiter:
    """A page request that waits; the smallest key is served first."""

    key: tuple[int, float, int]  # rank, due, arrival
    granted: asyncio.Future[None] = field(compare=False)
    arrived: float = field(compare=False)  # time.monotonic()


@contextmanager
def _clock_stopped() -> Iterator[None]:
    """Stop the waiting work's ``work_clock``; it gets its time left back."""
    clock = work_clock.get()
    if clock is None or clock.expired() or (when := clock.when()) is None:
        yield
        return
    loop = asyncio.get_running_loop()
    left = when - loop.time()
    clock.reschedule(None)
    try:
        yield
    finally:
        if not clock.expired():
            clock.reschedule(loop.time() + left)


class PageGate:
    """At most *limit* pages at a time, handed out by the claims' urgency."""

    def __init__(self, *, limit: int, telemetry: TelemetryPort = NO_TELEMETRY) -> None:
        self._limit = limit
        self._telemetry = telemetry
        self._in_use = 0
        self._waiters: list[_Waiter] = []
        self._arrivals = itertools.count()
        self._longest_wait = 0.0  # since the last take_longest_wait()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    def set_limit(self, limit: int) -> None:
        """Change the limit; a lower one takes no running page away."""
        self._limit = limit
        self._grant()

    def take_longest_wait(self) -> float:
        """The longest wait for a page since the last call, running waits
        included."""
        now = time.monotonic()
        running = [now - waiter.arrived for waiter in self._waiters]
        longest = max([self._longest_wait, *running])
        self._longest_wait = 0.0
        return longest

    @asynccontextmanager
    async def page(self, kind: PageKind, *, timeout: float) -> AsyncIterator[None]:
        """Hold one page for the work of the current claim.

        Raises ``PageBusy`` when no page comes free ``_MIN_WORK_S`` before
        the claim is due. Without a claim (Torznab searches, the scoring
        probes) the work counts as *kind*, due after its own *timeout*, and
        waits until a page is free.
        """
        claim = page_claim.get()
        latest = None if claim is None else claim.due - _MIN_WORK_S
        if claim is None:
            claim = PageClaim(kind, time.monotonic() + timeout)
        with self._telemetry.stage("browser_page_wait", kind=claim.kind) as wait:
            try:
                with _clock_stopped():
                    await self._acquire(claim, latest)
            except PageBusy:
                wait.outcome = "busy"
                raise
        try:
            with self._telemetry.stage("browser_page", kind=claim.kind):
                yield
        finally:
            self._release()

    async def _acquire(self, claim: PageClaim, latest: float | None) -> None:
        """Take a page for *claim*, waiting until *latest* at most."""
        if latest is not None and latest <= time.monotonic():
            raise PageBusy
        if self._in_use < self._limit and not self._waiters:
            self._in_use += 1
            return
        waiter = _Waiter(
            (_RANK[claim.kind], claim.due, next(self._arrivals)),
            asyncio.get_running_loop().create_future(),
            time.monotonic(),
        )
        self._waiters.append(waiter)
        try:
            # The loop's clock is time.monotonic()
            async with asyncio.timeout_at(latest):
                await waiter.granted
        except (asyncio.CancelledError, TimeoutError) as exc:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            elif not waiter.granted.cancelled():
                # Granted, but cut before it ran: the page goes on
                self._release()
            if isinstance(exc, TimeoutError):
                raise PageBusy from None
            raise
        finally:
            waited = time.monotonic() - waiter.arrived
            self._longest_wait = max(self._longest_wait, waited)

    def _release(self) -> None:
        self._in_use -= 1
        self._grant()

    def _grant(self) -> None:
        """Hand free pages to the most urgent waiters."""
        while self._waiters and self._in_use < self._limit:
            waiter = min(self._waiters)
            self._waiters.remove(waiter)
            self._in_use += 1
            waiter.granted.set_result(None)
