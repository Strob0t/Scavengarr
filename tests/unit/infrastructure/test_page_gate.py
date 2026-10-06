"""Tests for PageGate (the stealth browser's pages)."""

from __future__ import annotations

import asyncio
import time

from scavengarr.domain.ports.browser_fetcher import PageClaim, PageKind, page_claim
from scavengarr.infrastructure.browser.page_gate import PageGate
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

    async def test_waiters_get_pages_in_arrival_order(self) -> None:
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
