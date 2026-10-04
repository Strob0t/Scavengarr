"""Tests for the event-loop setup in composition."""

from __future__ import annotations

import asyncio

from scavengarr.interfaces.composition import use_eager_tasks


async def test_tasks_start_eagerly() -> None:
    """A task runs until its first await when it is created: tasks that
    finish without suspending (cache hits, guards) skip a trip through the
    event loop. The whole suite passes with eager loops (2026-10-04)."""
    loop = asyncio.get_running_loop()
    steps: list[str] = []

    async def _child() -> None:
        steps.append("child started")
        await asyncio.sleep(0)

    try:
        use_eager_tasks()
        task = asyncio.create_task(_child())
        steps.append("created")
        await task
    finally:
        loop.set_task_factory(None)

    assert steps == ["child started", "created"]
