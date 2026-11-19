"""Tests for GracefulShutdown."""

from __future__ import annotations

import asyncio

import pytest

from scavengarr.infrastructure.graceful_shutdown import (
    GracefulShutdown,
    WatchedRequest,
)


class TestRequestTracking:
    def test_starts_with_zero_active(self) -> None:
        gs = GracefulShutdown()
        assert gs.active_requests == 0

    def test_request_started_increments(self) -> None:
        gs = GracefulShutdown()
        gs.request_started()
        assert gs.active_requests == 1

    def test_request_finished_decrements(self) -> None:
        gs = GracefulShutdown()
        gs.request_started()
        gs.request_finished()
        assert gs.active_requests == 0

    def test_never_goes_negative(self) -> None:
        gs = GracefulShutdown()
        gs.request_finished()
        assert gs.active_requests == 0


class TestReadiness:
    def test_not_ready_initially(self) -> None:
        gs = GracefulShutdown()
        assert gs.is_ready is False

    def test_ready_after_mark(self) -> None:
        gs = GracefulShutdown()
        gs.mark_ready()
        assert gs.is_ready is True

    def test_not_ready_during_shutdown(self) -> None:
        gs = GracefulShutdown()
        gs.mark_ready()
        assert gs.is_ready is True
        gs._shutting_down = True
        assert gs.is_ready is False


class TestDrain:
    @pytest.mark.asyncio
    async def test_drain_with_no_requests(self) -> None:
        gs = GracefulShutdown()
        await gs.wait_for_drain(timeout=1.0)
        assert gs.is_shutting_down is True

    @pytest.mark.asyncio
    async def test_drain_waits_for_active_requests(self) -> None:
        gs = GracefulShutdown()
        gs.request_started()

        drained = False

        async def _finish_request():
            nonlocal drained
            await asyncio.sleep(0.05)
            gs.request_finished()

        async def _drain():
            nonlocal drained
            await gs.wait_for_drain(timeout=2.0)
            drained = True

        await asyncio.gather(_finish_request(), _drain())
        assert drained is True
        assert gs.active_requests == 0

    @pytest.mark.asyncio
    async def test_drain_times_out(self) -> None:
        gs = GracefulShutdown()
        gs.request_started()
        # Don't finish the request — drain should timeout
        await gs.wait_for_drain(timeout=0.05)
        assert gs.active_requests == 1
        assert gs.is_shutting_down is True


class TestWatchedRequests:
    """The requests in flight that the stuck-request check reads."""

    async def test_a_request_is_watched_until_it_is_forgotten(self) -> None:
        gs = GracefulShutdown()
        task = asyncio.current_task()
        assert task is not None
        request = WatchedRequest(task=task, route="torznab", started=1.0)

        gs.watch_request(request)
        assert gs.watched_requests == (request,)
        gs.forget_request(request)
        assert gs.watched_requests == ()

    async def test_forgetting_twice_is_harmless(self) -> None:
        gs = GracefulShutdown()
        task = asyncio.current_task()
        assert task is not None
        request = WatchedRequest(task=task, route="other", started=1.0)
        gs.watch_request(request)

        gs.forget_request(request)
        gs.forget_request(request)
        assert gs.watched_requests == ()
