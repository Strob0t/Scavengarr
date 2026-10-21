"""Background tasks log their death (OpenSpec add-stall-watchdog, group 1)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import get_args

import pytest
from structlog.testing import capture_logs

from scavengarr.domain.ports.telemetry import BACKGROUND_TASKS, BackgroundTaskName
from scavengarr.infrastructure.telemetry import Telemetry
from scavengarr.infrastructure.telemetry.background import stop_task, watch_task

_DEATHS = "scavengarr_background_task_deaths_total"


def _deaths(telemetry: Telemetry, task: str) -> float | None:
    return telemetry.registry.get_sample_value(_DEATHS, {"task": task})


async def _settle(task: asyncio.Task[None]) -> None:
    """Wait for *task* to end, then one loop turn for its done-callbacks."""
    with suppress(BaseException):
        await task
    await asyncio.sleep(0)


async def _raises() -> None:
    raise RuntimeError("boom")


async def _returns() -> None:
    return None


async def _waits() -> None:
    await asyncio.Event().wait()


class TestWatchTask:
    async def test_a_task_that_raises_is_logged_and_counted_once(self) -> None:
        telemetry = Telemetry()
        with capture_logs() as logs:
            task = watch_task(asyncio.create_task(_raises()), "loop_lag", telemetry)
            await _settle(task)

        died = [e for e in logs if e["event"] == "background_task_died"]
        assert len(died) == 1
        assert died[0]["task"] == "loop_lag"
        assert died[0]["error"] == "RuntimeError"
        assert died[0]["log_level"] == "error"
        assert isinstance(died[0]["exc_info"], RuntimeError)
        assert str(died[0]["exc_info"]) == "boom"
        assert _deaths(telemetry, "loop_lag") == 1.0
        assert _deaths(telemetry, "scoring") == 0.0

    async def test_a_cancelled_task_logs_and_counts_nothing(self) -> None:
        telemetry = Telemetry()
        with capture_logs() as logs:
            task = watch_task(asyncio.create_task(_waits()), "pass_keeper", telemetry)
            await asyncio.sleep(0)
            task.cancel()
            await _settle(task)

        assert task.cancelled()
        assert [e for e in logs if e["event"] == "background_task_died"] == []
        assert _deaths(telemetry, "pass_keeper") == 0.0

    async def test_a_task_that_returns_logs_and_counts_nothing(self) -> None:
        telemetry = Telemetry()
        with capture_logs() as logs:
            task = watch_task(
                asyncio.create_task(_returns()), "browser_warmup", telemetry
            )
            await _settle(task)

        assert task.done() and task.exception() is None
        assert [e for e in logs if e["event"] == "background_task_died"] == []
        assert _deaths(telemetry, "browser_warmup") == 0.0

    async def test_the_task_is_handed_back(self) -> None:
        task = asyncio.create_task(_returns())

        assert watch_task(task, "scoring", Telemetry()) is task
        await _settle(task)


def test_every_task_name_has_a_series_from_the_start() -> None:
    """A zero series per name: ``increase()`` sees the first death."""
    telemetry = Telemetry()

    assert {
        name: _deaths(telemetry, name) for name in get_args(BackgroundTaskName)
    } == {name: 0.0 for name in get_args(BackgroundTaskName)}
    assert set(get_args(BackgroundTaskName)) == {
        "loop_lag",
        "page_budget",
        "plugin_health",
        "scoring",
        "browser_warmup",
        "pass_keeper",
        "hoster_state_store",
        "plugin_history",
    }
    # The values the zero series come from are the Literal's names, once each
    assert sorted(BACKGROUND_TASKS) == sorted(get_args(BackgroundTaskName))


class TestStopTask:
    """The shutdown stops each task without its exception (row 158): a task
    that died before would end the shutdown at its await."""

    async def test_a_running_task_is_cancelled(self) -> None:
        telemetry = Telemetry()
        task = watch_task(asyncio.create_task(_waits()), "loop_lag", telemetry)
        await asyncio.sleep(0)
        with capture_logs() as logs:
            await stop_task(task)
            await asyncio.sleep(0)

        assert task.cancelled()
        assert [e for e in logs if e["event"] == "background_task_died"] == []
        assert _deaths(telemetry, "loop_lag") == 0.0

    async def test_a_dead_task_raises_nothing_and_is_not_counted_again(self) -> None:
        telemetry = Telemetry()
        task = watch_task(asyncio.create_task(_raises()), "page_budget", telemetry)
        await _settle(task)

        with capture_logs() as logs:
            await stop_task(task)

        assert isinstance(task.exception(), RuntimeError)
        assert [e for e in logs if e["event"] == "background_task_died"] == []
        assert _deaths(telemetry, "page_budget") == 1.0

    async def test_a_cancelled_task_raises_nothing(self) -> None:
        task = watch_task(asyncio.create_task(_waits()), "scoring", Telemetry())
        await asyncio.sleep(0)
        task.cancel()
        await _settle(task)

        await stop_task(task)

        assert task.cancelled()

    async def test_a_returned_task_raises_nothing(self) -> None:
        task = watch_task(
            asyncio.create_task(_returns()), "browser_warmup", Telemetry()
        )
        await _settle(task)

        await stop_task(task)

        assert task.exception() is None

    async def test_a_cancelled_shutdown_still_propagates(self) -> None:
        """The caller's own cancellation is not swallowed while the task
        takes its time to end."""
        release = asyncio.Event()

        async def _slow_to_end() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                raise

        task = watch_task(
            asyncio.create_task(_slow_to_end()), "pass_keeper", Telemetry()
        )
        await asyncio.sleep(0)
        stopping = asyncio.create_task(stop_task(task))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        stopping.cancel()

        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert not task.done()
        release.set()
        await _settle(task)
        assert task.cancelled()
