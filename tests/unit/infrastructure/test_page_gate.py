"""Tests for PageGate (the stealth browser's pages)."""

from __future__ import annotations

import asyncio
import time

import pytest

from scavengarr.domain.ports.browser_fetcher import PageClaim, PageKind, page_claim
from scavengarr.infrastructure.browser.page_gate import (
    PageBusy,
    PageGate,
    work_clock,
)
from scavengarr.infrastructure.telemetry import Telemetry


async def _hold(
    gate: PageGate,
    release: asyncio.Event,
    order: list[str] | None = None,
    name: str = "",
    kind: PageKind = "plugin",
) -> None:
    """Take a page, note *name* in *order*, keep the page until *release*."""
    async with gate.page(kind, timeout=30):
        if order is not None:
            order.append(name)
        await release.wait()


async def _settle() -> None:
    """Let every task that can run do so."""
    for _ in range(5):
        await asyncio.sleep(0)


class TestLimit:
    async def test_pages_are_bounded_by_the_limit(self) -> None:
        gate = PageGate(limit=2)
        release = asyncio.Event()
        tasks = [asyncio.create_task(_hold(gate, release)) for _ in range(5)]
        await _settle()

        assert gate.in_use == 2
        assert gate.waiting == 3

        release.set()
        await asyncio.gather(*tasks)
        assert gate.in_use == 0
        assert gate.waiting == 0

    async def test_equal_claims_get_pages_in_arrival_order(self) -> None:
        gate = PageGate(limit=1)
        first = asyncio.Event()
        rest = asyncio.Event()
        order: list[str] = []
        holder = asyncio.create_task(_hold(gate, first, order, "holder"))
        await _settle()
        waiters = [
            asyncio.create_task(_hold(gate, rest, order, name))
            for name in ("a", "b", "c")
        ]
        await _settle()

        first.set()
        rest.set()
        await asyncio.gather(holder, *waiters)

        assert order == ["holder", "a", "b", "c"]


class TestCancellation:
    async def test_a_cancelled_waiter_leaves_the_queue(self) -> None:
        gate = PageGate(limit=1)
        release = asyncio.Event()
        order: list[str] = []
        holder = asyncio.create_task(_hold(gate, release, order, "holder"))
        await _settle()
        gone = asyncio.create_task(_hold(gate, release, order, "gone"))
        stays = asyncio.create_task(_hold(gate, release, order, "stays"))
        await _settle()

        gone.cancel()
        await _settle()
        assert gate.waiting == 1

        release.set()
        await asyncio.gather(holder, stays)
        assert order == ["holder", "stays"]
        assert gate.in_use == 0

    async def test_a_waiter_cancelled_after_its_grant_passes_the_page_on(
        self,
    ) -> None:
        gate = PageGate(limit=1)
        release = asyncio.Event()
        release.set()
        order: list[str] = []
        async with gate.page("plugin", timeout=30):
            granted = asyncio.create_task(_hold(gate, release, order, "granted"))
            nxt = asyncio.create_task(_hold(gate, release, order, "next"))
            await _settle()

        # The page went to "granted" on release; it is cancelled before it ran
        granted.cancel()
        await nxt

        assert order == ["next"]
        assert granted.cancelled()
        assert gate.in_use == 0


class TestMetrics:
    async def test_wait_and_work_are_recorded_by_kind(self) -> None:
        telemetry = Telemetry()
        gate = PageGate(limit=1, telemetry=telemetry)
        release = asyncio.Event()
        holder = asyncio.create_task(_hold(gate, release, kind="capture"))
        await _settle()
        waiter = asyncio.create_task(_hold(gate, release, kind="capture"))
        await _settle()

        release.set()
        await asyncio.gather(holder, waiter)

        def sample(name: str, **labels: str) -> float | None:
            return telemetry.registry.get_sample_value(name, labels)

        assert (
            sample("scavengarr_browser_page_wait_total", kind="capture", outcome="ok")
            == 2
        )
        assert (
            sample("scavengarr_browser_page_total", kind="capture", outcome="ok") == 2
        )
        assert sample("scavengarr_browser_page_seconds_count", kind="capture") == 2

    async def test_a_waiter_cut_while_waiting_is_recorded_as_cut(self) -> None:
        telemetry = Telemetry()
        gate = PageGate(limit=1, telemetry=telemetry)
        release = asyncio.Event()
        holder = asyncio.create_task(_hold(gate, release))
        await _settle()
        cut = asyncio.create_task(_hold(gate, release))
        await _settle()

        cut.cancel()
        await _settle()
        release.set()
        await holder

        assert (
            telemetry.registry.get_sample_value(
                "scavengarr_browser_page_wait_total",
                {"kind": "plugin", "outcome": "cut"},
            )
            == 1
        )

    async def test_the_claim_names_the_kind(self) -> None:
        telemetry = Telemetry()
        gate = PageGate(limit=1, telemetry=telemetry)
        page_claim.set(PageClaim("play", time.monotonic() + 15))

        async with gate.page("capture", timeout=15):
            pass

        assert (
            telemetry.registry.get_sample_value(
                "scavengarr_browser_page_total", {"kind": "play", "outcome": "ok"}
            )
            == 1
        )


def _claim(kind: PageKind, due_in: float) -> None:
    """Claim the current task's pages as *kind*, due in *due_in* seconds."""
    page_claim.set(PageClaim(kind, time.monotonic() + due_in))


class TestDeadlines:
    """Work that cannot finish before it is due gets no page (it would burn
    CPU for an answer that has gone out)."""

    async def test_a_waiter_gives_up_shortly_before_its_due_time(self) -> None:
        gate = PageGate(limit=1)
        release = asyncio.Event()
        holder = asyncio.create_task(_hold(gate, release))
        await _settle()
        _claim("capture", 3.05)  # 3 s before it is due: the work's minimum
        started = time.monotonic()

        with pytest.raises(PageBusy):
            async with gate.page("capture", timeout=15):
                pass

        assert time.monotonic() - started < 1
        assert gate.waiting == 0
        release.set()
        await holder
        assert gate.in_use == 0

    async def test_work_due_too_soon_gets_no_free_page(self) -> None:
        gate = PageGate(limit=1)
        _claim("plugin", 1.0)

        with pytest.raises(PageBusy):
            async with gate.page("plugin", timeout=30):
                pass

        assert gate.in_use == 0

    async def test_a_busy_wait_is_recorded(self) -> None:
        telemetry = Telemetry()
        gate = PageGate(limit=1, telemetry=telemetry)
        _claim("background", 0.5)

        with pytest.raises(PageBusy):
            async with gate.page("capture", timeout=15):
                pass

        assert (
            telemetry.registry.get_sample_value(
                "scavengarr_browser_page_wait_total",
                {"kind": "background", "outcome": "busy"},
            )
            == 1
        )


class TestWorkClock:
    """The resolve timeout measures the work, not the wait for a page: a
    capture that waited 9 of its 10 s timed out and tripped the breaker of
    a healthy hoster (sixth round, 2026-10-06)."""

    @staticmethod
    async def _timed_work(gate: PageGate, timeout: float, work: float) -> str:
        async with asyncio.timeout(timeout) as clock:
            work_clock.set(clock)
            async with gate.page("capture", timeout=15):
                await asyncio.sleep(work)
                return "done"

    async def test_the_clock_stops_while_waiting(self) -> None:
        gate = PageGate(limit=1)
        release = asyncio.Event()
        holder = asyncio.create_task(_hold(gate, release))
        await _settle()
        work = asyncio.create_task(self._timed_work(gate, 0.1, 0.0))

        await asyncio.sleep(0.3)
        release.set()

        assert await work == "done"
        await holder

    async def test_the_work_keeps_only_its_remaining_time(self) -> None:
        gate = PageGate(limit=1)
        release = asyncio.Event()
        holder = asyncio.create_task(_hold(gate, release))
        await _settle()
        work = asyncio.create_task(self._timed_work(gate, 0.1, 0.5))

        await asyncio.sleep(0.2)
        release.set()

        with pytest.raises(TimeoutError):
            await work
        await holder
        assert gate.in_use == 0


async def _claimed_hold(
    gate: PageGate,
    release: asyncio.Event,
    order: list[str],
    name: str,
    kind: PageKind,
    due_in: float,
) -> None:
    """``_hold`` under a claim of *kind*, due in *due_in* seconds."""
    _claim(kind, due_in)
    await _hold(gate, release, order, name)


class TestOrder:
    """The next free page goes to playback, then to the earliest due work;
    background work only gets one while nobody else waits. kinoger's pages
    queued behind the captures of its own answer until its 30 s timeout
    (sixth round, 2026-10-06)."""

    @staticmethod
    async def _served(waiters: list[tuple[str, PageKind, float]]) -> list[str]:
        """The order in which *waiters* (name, kind, due in) get one page."""
        gate = PageGate(limit=1)
        first = asyncio.Event()
        rest = asyncio.Event()
        order: list[str] = []
        holder = asyncio.create_task(_hold(gate, first, order, "holder"))
        await _settle()
        tasks = [
            asyncio.create_task(_claimed_hold(gate, rest, order, name, kind, due_in))
            for name, kind, due_in in waiters
        ]
        await _settle()
        first.set()
        rest.set()
        await asyncio.gather(holder, *tasks)
        return order[1:]

    async def test_the_earliest_due_work_goes_first(self) -> None:
        order = await self._served(
            [
                ("capture", "capture", 60),
                ("plugin", "plugin", 30),
                ("older capture", "capture", 40),
            ]
        )

        assert order == ["plugin", "older capture", "capture"]

    async def test_playback_goes_first(self) -> None:
        order = await self._served([("plugin", "plugin", 10), ("play", "play", 15)])

        assert order == ["play", "plugin"]

    async def test_background_work_goes_last(self) -> None:
        order = await self._served(
            [("background", "background", 10), ("capture", "capture", 60)]
        )

        assert order == ["capture", "background"]
