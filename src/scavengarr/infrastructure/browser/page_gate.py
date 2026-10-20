"""The stealth browser's pages: how many may be open, and who gets the next.

Plugin pages behind Cloudflare, hoster captures and link-outs share one
headful browser, and a page costs about 200 MB and much CPU. The work's
claim (``page_claim``) names what a page is for and when the work is due.
The next free page goes to playback, then to the earliest due work (a
search's plugin pages before the captures of its answer), and to
background work only while nobody else waits; a running page is never
taken away. Work that cannot start ``_MIN_WORK_S`` before it is due gets
no page (``PageBusy``), and the timeout of a resolution (``work_clock``)
stops while it waits. A page that shows a challenge marks the resolution
it works for (``challenge_mark``), and passes it once it got past. The
wait for a page and the work on it are recorded by kind
(``browser_page_wait``, ``browser_page``). A page handed on past its
work's block (``Hold``) counts until it is ended. ``PageBudget`` changes
the limit while pages are in use. See
``docs/target/decisions/0010-adaptive-page-budget.md``.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from scavengarr.domain.ports.browser_fetcher import PageClaim, PageKind, page_claim
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.captcha.detect import ChallengeKind

# Who goes first: playback, then plugin pages and captures by due time,
# background work last
_RANK: dict[PageKind, int] = {"play": 0, "plugin": 1, "capture": 1, "background": 2}

# Work that gets its page later than this before it is due rarely finishes:
# Filemoon's capture, the shortest browser work, took 1.5-2 s (2026-10-06)
_MIN_WORK_S = 3.0

# The timeout of the work that asks for a page (the registry's resolve
# timeout): it measures the work, so it stops while the work waits
work_clock: ContextVar[asyncio.Timeout | None] = ContextVar("work_clock", default=None)

# What a resolution's page showed: a challenge the detector knows, or a
# request the resolver names as its hoster's own captcha
type ResolutionChallenge = ChallengeKind | Literal["hoster_captcha"]


@dataclass
class ChallengeMark:
    """The challenge a resolution's pages showed, the first one noted, and
    whether one still stands: noted and not got past since (``open``). A
    page past its challenge gives a verdict on the link, so a dead link
    behind it is dead, not a challenge (code review 2026-10-12, finding 2)."""

    kind: ResolutionChallenge | None = None
    open: bool = False

    def note(self, kind: ResolutionChallenge) -> None:
        """Note *kind* (the first one stays the kind); it stands."""
        if self.kind is None:
            self.kind = kind
        self.open = True

    def passed(self) -> None:
        """The page got past the challenge that stood."""
        self.open = False


# The mark of the resolution that runs (the registry's attempt): set by the
# registry, marked by the browser when a page shows a challenge, so the
# registry tells a challenged capture from a timeout (react-to-challenges)
challenge_mark: ContextVar[ChallengeMark | None] = ContextVar(
    "challenge_mark", default=None
)


def mark_resolution_challenge(kind: ResolutionChallenge) -> None:
    """Mark the running resolution's challenge; outside one (a plugin's
    page) nothing."""
    mark = challenge_mark.get()
    if mark is not None:
        mark.note(kind)


def mark_resolution_passed() -> None:
    """The running resolution's page got past its challenge (an interstitial
    cleared, a widget's token came) or showed its verdict on the link (the
    file is gone); outside one nothing."""
    mark = challenge_mark.get()
    if mark is not None:
        mark.passed()


class PageBusy(Exception):
    """No page came free in time for the work's claim."""


class Hold:
    """What an ``async with`` holds of the stealth browser: one of the
    gate's pages (``PageGate.page()``) or the browser's lease
    (``SharedBrowserPool.lease()``). It ends with the block, unless the
    block hands it on (``hand_on()``) to work that outlives the block and
    ends it there (``end()``): a capture's page that stays open after its
    stream went out, to keep its state, counts until it closes (row 107)."""

    def __init__(self, end: Callable[[], None]) -> None:
        self._end: Callable[[], None] | None = end
        self._handed_on = False

    def __enter__(self) -> Hold:
        return self

    def __exit__(self, *_: object) -> None:
        if not self._handed_on:
            self.end()

    def hand_on(self) -> None:
        """Hold on past the block, until ``end()``."""
        self._handed_on = True

    def end(self) -> None:
        """End the hold; once, a second call does nothing."""
        end, self._end = self._end, None
        if end is not None:
            end()


@dataclass(order=True)
class _Waiter:
    """A page request that waits; the smallest key is served first."""

    key: tuple[int, float, int]  # rank, due, arrival
    granted: asyncio.Future[None] = field(compare=False)
    arrived: float = field(compare=False)  # time.monotonic()


@contextmanager
def clock_stopped() -> Iterator[None]:
    """Stop the waiting work's ``work_clock``; it gets its time left back.
    For the waits that are the browser's, not the work's: a page, the
    shared browser's restart."""
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
    async def page(self, kind: PageKind, *, timeout: float) -> AsyncIterator[Hold]:
        """Hold one page for the work of the current claim, until the block
        ends or, handed on, its ``Hold`` (the work on the page is recorded
        until the block ends).

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
                with clock_stopped():
                    await self._acquire(claim, latest)
            except PageBusy:
                wait.outcome = "busy"
                raise
        with (
            Hold(self._release) as hold,
            self._telemetry.stage("browser_page", kind=claim.kind),
        ):
            yield hold

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
