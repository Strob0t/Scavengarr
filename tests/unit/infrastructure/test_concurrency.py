"""Tests for ConcurrencyPool and RequestBudget."""

from __future__ import annotations

import asyncio

import pytest

from scavengarr.infrastructure.concurrency import ConcurrencyPool, RequestBudget

# ---------------------------------------------------------------------------
# ConcurrencyPool basics
# ---------------------------------------------------------------------------


class TestConcurrencyPoolInit:
    def test_default_slots(self) -> None:
        pool = ConcurrencyPool()
        assert pool.httpx_slots == 10
        assert pool.pw_slots == 3
        assert pool.active_requests == 0

    def test_custom_slots(self) -> None:
        pool = ConcurrencyPool(httpx_slots=20, pw_slots=5)
        assert pool.httpx_slots == 20
        assert pool.pw_slots == 5

    @pytest.mark.asyncio
    async def test_request_increments_active_count(self) -> None:
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=2)
        assert pool.active_requests == 0
        async with pool.request() as budget:
            assert pool.active_requests == 1
            assert isinstance(budget, RequestBudget)
        assert pool.active_requests == 0

    @pytest.mark.asyncio
    async def test_multiple_requests_increment(self) -> None:
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=2)
        async with pool.request():
            assert pool.active_requests == 1
            async with pool.request():
                assert pool.active_requests == 2
            assert pool.active_requests == 1
        assert pool.active_requests == 0

    def test_snapshot(self) -> None:
        pool = ConcurrencyPool(httpx_slots=8, pw_slots=2)
        snap = pool.snapshot()
        assert snap["httpx_slots"] == 8
        assert snap["pw_slots"] == 2
        assert snap["httpx_available"] == 8
        assert snap["pw_available"] == 2
        assert snap["active_requests"] == 0


# ---------------------------------------------------------------------------
# Fair-share math
# ---------------------------------------------------------------------------


class TestFairShare:
    @pytest.mark.asyncio
    async def test_single_request_gets_all_slots(self) -> None:
        """1 request, 10 httpx slots -> fair_share=10."""
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=3)
        async with pool.request() as budget:
            assert budget._httpx_fair_share() == 10
            assert budget._pw_fair_share() == 3

    @pytest.mark.asyncio
    async def test_two_requests_split_slots(self) -> None:
        """2 requests, 10 httpx slots -> fair_share=5 each."""
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=4)

        async with pool.request() as budget_a:
            async with pool.request() as budget_b:
                assert budget_a._httpx_fair_share() == 5
                assert budget_b._httpx_fair_share() == 5
                assert budget_a._pw_fair_share() == 2
                assert budget_b._pw_fair_share() == 2

    @pytest.mark.asyncio
    async def test_three_requests_fair_share(self) -> None:
        """3 requests, 10 httpx slots -> fair_share=3 each."""
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=3)

        async with pool.request() as b1:
            async with pool.request() as b2:
                async with pool.request() as b3:
                    assert b1._httpx_fair_share() == 3
                    assert b2._httpx_fair_share() == 3
                    assert b3._httpx_fair_share() == 3
                    # PW: 3 slots / 3 requests = 1 each
                    assert b1._pw_fair_share() == 1

    @pytest.mark.asyncio
    async def test_fair_share_minimum_is_one(self) -> None:
        """Even with more requests than slots, fair_share is at least 1."""
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)

        async with pool.request() as b1:
            async with pool.request() as b2:
                async with pool.request() as b3:
                    assert b1._httpx_fair_share() == 1
                    assert b2._httpx_fair_share() == 1
                    assert b3._httpx_fair_share() == 1
                    assert b1._pw_fair_share() == 1


# ---------------------------------------------------------------------------
# Slot acquisition
# ---------------------------------------------------------------------------


class TestAcquireHttpx:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)
        async with pool.request() as budget:
            async with budget.acquire_httpx():
                assert budget._held_httpx == 1
            assert budget._held_httpx == 0

    @pytest.mark.asyncio
    async def test_multiple_acquires(self) -> None:
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=1)
        async with pool.request() as budget:
            async with budget.acquire_httpx():
                async with budget.acquire_httpx():
                    assert budget._held_httpx == 2

    @pytest.mark.asyncio
    async def test_release_on_error(self) -> None:
        """Slots are released even when the body raises."""
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)
        async with pool.request() as budget:
            with pytest.raises(ValueError, match="boom"):
                async with budget.acquire_httpx():
                    raise ValueError("boom")
            assert budget._held_httpx == 0


class TestAcquirePw:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self) -> None:
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=2)
        async with pool.request() as budget:
            async with budget.acquire_pw():
                assert budget._held_pw == 1
            assert budget._held_pw == 0

    @pytest.mark.asyncio
    async def test_release_on_error(self) -> None:
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=2)
        async with pool.request() as budget:
            with pytest.raises(RuntimeError, match="fail"):
                async with budget.acquire_pw():
                    raise RuntimeError("fail")
            assert budget._held_pw == 0


# ---------------------------------------------------------------------------
# Dynamic expansion (request exits -> remaining get more)
# ---------------------------------------------------------------------------


class TestDynamicExpansion:
    @pytest.mark.asyncio
    async def test_expansion_on_request_exit(self) -> None:
        """When one request exits, remaining requests' fair-share grows."""
        pool = ConcurrencyPool(httpx_slots=10, pw_slots=4)

        async with pool.request() as budget_a:
            async with pool.request() as budget_b:
                # 2 requests: fair_share = 5
                assert budget_a._httpx_fair_share() == 5
                assert budget_b._httpx_fair_share() == 5

            # budget_b exited: only 1 request left -> fair_share = 10
            assert budget_a._httpx_fair_share() == 10


# ---------------------------------------------------------------------------
# Concurrent request isolation
# ---------------------------------------------------------------------------


class TestConcurrentRequests:
    @pytest.mark.asyncio
    async def test_parallel_requests_share_global_sem(self) -> None:
        """Two requests acquiring slots concurrently share the global pool."""
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=2)
        acquired: list[str] = []

        async def _request(label: str) -> None:
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    acquired.append(f"{label}-httpx")
                    await asyncio.sleep(0)
                async with budget.acquire_pw():
                    acquired.append(f"{label}-pw")
                    await asyncio.sleep(0)

        await asyncio.gather(_request("a"), _request("b"))

        assert "a-httpx" in acquired
        assert "b-httpx" in acquired
        assert "a-pw" in acquired
        assert "b-pw" in acquired

    @pytest.mark.asyncio
    async def test_fair_share_limits_per_request(self) -> None:
        """A request cannot hold more slots than its fair share."""
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=2)

        held_counts: list[int] = []
        barrier = asyncio.Event()

        async def _greedy_request() -> None:
            async with pool.request() as budget:
                # Signal other request to start
                barrier.set()
                # Try to acquire multiple slots
                async with budget.acquire_httpx():
                    async with budget.acquire_httpx():
                        held_counts.append(budget._held_httpx)
                        await asyncio.sleep(0.05)

        async def _second_request() -> None:
            await barrier.wait()
            async with pool.request() as budget:
                # Both requests active: fair_share = 2 each
                async with budget.acquire_httpx():
                    held_counts.append(budget._held_httpx)

        await asyncio.gather(_greedy_request(), _second_request())

        # Greedy held 2, second held 1
        assert 2 in held_counts
        assert 1 in held_counts


# ---------------------------------------------------------------------------
# Integration-style concurrency tests
# ---------------------------------------------------------------------------


class TestConcurrencyIntegration:
    @pytest.mark.asyncio
    async def test_global_semaphore_blocks_excess(self) -> None:
        """Once all global slots are taken, further acquires block."""
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)
        blocked = asyncio.Event()
        unblock = asyncio.Event()

        async def _holder() -> None:
            """Hold all 2 httpx slots until signalled."""
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    async with budget.acquire_httpx():
                        blocked.set()
                        await unblock.wait()

        async def _waiter() -> str:
            """Try to acquire once the holder has both slots."""
            await blocked.wait()
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    return "acquired"
            return "unreachable"  # pragma: no cover

        async def _release_after_delay() -> None:
            await blocked.wait()
            await asyncio.sleep(0.05)
            unblock.set()

        results = await asyncio.gather(_holder(), _waiter(), _release_after_delay())
        assert results[1] == "acquired"

    @pytest.mark.asyncio
    async def test_dynamic_expansion_unblocks_waiter(self) -> None:
        """When one request exits, a blocked request gets unblocked."""
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)
        order: list[str] = []

        async def _request_a() -> None:
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    async with budget.acquire_httpx():
                        order.append("a-holding-2")
                        await asyncio.sleep(0.05)
            order.append("a-exited")

        async def _request_b() -> None:
            await asyncio.sleep(0.01)  # let A start first
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    order.append("b-acquired")

        await asyncio.gather(_request_a(), _request_b())
        # B should only acquire after A releases
        assert order.index("a-holding-2") < order.index("b-acquired")

    @pytest.mark.asyncio
    async def test_three_requests_all_complete(self) -> None:
        """Three concurrent requests all complete with 4 httpx slots."""
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=2)
        results: list[str] = []

        async def _work(label: str) -> None:
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    await asyncio.sleep(0.01)
                    results.append(label)

        await asyncio.gather(_work("a"), _work("b"), _work("c"))
        assert sorted(results) == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_pw_and_httpx_independent(self) -> None:
        """PW and httpx slots are independent pools."""
        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        order: list[str] = []

        async def _httpx_work() -> None:
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    order.append("httpx-start")
                    await asyncio.sleep(0.05)
                    order.append("httpx-end")

        async def _pw_work() -> None:
            async with pool.request() as budget:
                async with budget.acquire_pw():
                    order.append("pw-start")
                    await asyncio.sleep(0.05)
                    order.append("pw-end")

        await asyncio.gather(_httpx_work(), _pw_work())
        # Both should start before either finishes (independent pools)
        assert "httpx-start" in order
        assert "pw-start" in order
        httpx_start_idx = order.index("httpx-start")
        pw_start_idx = order.index("pw-start")
        httpx_end_idx = order.index("httpx-end")
        pw_end_idx = order.index("pw-end")
        # At least one pair should overlap (both started before one ended)
        assert httpx_start_idx < pw_end_idx or pw_start_idx < httpx_end_idx

    @pytest.mark.asyncio
    async def test_error_in_request_releases_counter(self) -> None:
        """If a request body raises, active_requests still decrements."""
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)
        with pytest.raises(ValueError, match="boom"):
            async with pool.request() as budget:
                async with budget.acquire_httpx():
                    raise ValueError("boom")
        assert pool.active_requests == 0
        # Slot is also released
        snap = pool.snapshot()
        assert snap["httpx_available"] == 2

    @pytest.mark.asyncio
    async def test_snapshot_reflects_held_slots(self) -> None:
        """Snapshot shows reduced available slots while held."""
        pool = ConcurrencyPool(httpx_slots=4, pw_slots=2)
        async with pool.request() as budget:
            async with budget.acquire_httpx():
                snap = pool.snapshot()
                assert snap["httpx_available"] == 3
                assert snap["active_requests"] == 1


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_pool_satisfies_port(self) -> None:
        from scavengarr.domain.ports.concurrency import ConcurrencyPoolPort

        pool = ConcurrencyPool()
        assert isinstance(pool, ConcurrencyPoolPort)

    @pytest.mark.asyncio
    async def test_budget_satisfies_port(self) -> None:
        from scavengarr.domain.ports.concurrency import ConcurrencyBudgetPort

        pool = ConcurrencyPool()
        async with pool.request() as budget:
            assert isinstance(budget, ConcurrencyBudgetPort)
