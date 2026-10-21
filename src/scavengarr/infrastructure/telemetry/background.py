"""A long-lived background task logs its death (OpenSpec add-stall-watchdog),
and the shutdown stops it without its exception.

Without it, a task that ends with an exception shows nothing until the
shutdown awaits it: the loop lag monitor has no guard, and the writers of
the hoster state and the plugin history guard only their write.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TypeVar

import structlog

from scavengarr.domain.ports.telemetry import BackgroundTaskName, TelemetryPort

log = structlog.get_logger(__name__)

T = TypeVar("T")


def watch_task(
    task: asyncio.Task[T], name: BackgroundTaskName, telemetry: TelemetryPort
) -> asyncio.Task[T]:
    """Log and count *task*'s end by an exception; return *task*.

    A task that ends with an exception logs ``background_task_died`` once
    with *name* and the exception and counts it under *name*; a cancelled
    task and one that returns log and count nothing.
    """
    task.add_done_callback(partial(_on_done, name, telemetry))
    return task


def _on_done(
    name: BackgroundTaskName, telemetry: TelemetryPort, task: asyncio.Task[T]
) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is None:
        return
    log.error(
        "background_task_died", task=name, error=type(error).__name__, exc_info=error
    )
    telemetry.record_background_task_death(name)


async def stop_task(task: asyncio.Task[T]) -> None:
    """Cancel *task* and wait for its end without raising its exception.

    A task that died before the shutdown would re-raise at a plain
    ``await`` and end the shutdown there; its death is logged and its
    exception retrieved by ``watch_task``. A cancellation of the caller
    still propagates (``asyncio.wait`` leaves *task* to end on its own).
    """
    task.cancel()
    await asyncio.wait({task})
