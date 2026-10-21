"""Background tasks log their death (OpenSpec add-stall-watchdog, group 1)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import get_args

from structlog.testing import capture_logs

from scavengarr.domain.ports.telemetry import BACKGROUND_TASKS, BackgroundTaskName
from scavengarr.infrastructure.telemetry import Telemetry
from scavengarr.infrastructure.telemetry.background import watch_task

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
