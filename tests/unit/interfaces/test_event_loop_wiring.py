"""Tests for the event-loop setup in composition."""

from __future__ import annotations

import asyncio

import pytest

from scavengarr.interfaces.composition import eager_task_factory, use_eager_tasks


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


def test_tasks_start_eagerly_on_uvloop() -> None:
    """uvicorn runs on uvloop. On Python 3.13 asyncio's eager factory
    refused uvloop's ``eager_start=None`` (the app did not start), on 3.14
    that ``None`` started every task lazily (code review, 2026-10-06)."""
    uvloop = pytest.importorskip("uvloop")
    steps: list[str] = []

    async def _child() -> None:
        steps.append("child started")

    async def _main() -> None:
        use_eager_tasks()
        task = asyncio.get_running_loop().create_task(_child())
        steps.append("created")
        await task

    uvloop.run(_main())

    assert steps == ["child started", "created"]


@pytest.mark.parametrize(
    ("eager_start", "started_at_once"), [(None, True), (True, True), (False, False)]
)
async def test_the_factory_takes_uvloops_eager_start(
    eager_start: bool | None, started_at_once: bool
) -> None:
    """uvloop 0.23 hands the factory ``eager_start=None``."""
    steps: list[str] = []

    async def _child() -> None:
        steps.append("child started")

    task = eager_task_factory(
        asyncio.get_running_loop(), _child(), eager_start=eager_start
    )
    assert (steps == ["child started"]) is started_at_once
    await task
