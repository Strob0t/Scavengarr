"""Tests for the event-loop setup in composition."""

from __future__ import annotations

import asyncio

import anyio
import httpx
import pytest

from scavengarr.infrastructure.config import AppConfig
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.composition import configure_event_loop


def test_a_handler_may_suspend_in_a_cancel_scope_at_once() -> None:
    """httpcore's connection lock suspends inside an anyio cancel scope.
    The app's own eager task factory started the task of Starlette's
    middleware eagerly, and anyio lost that scope: every proxied HLS
    variant answered 500 ("Attempted to exit a cancel scope that isn't the
    current tasks's current cancel scope", production, 2026-10-06)."""
    uvloop = pytest.importorskip("uvloop")
    app = create_app(AppConfig())

    @app.get("/test/cancel-scope")
    async def _suspends_in_a_scope() -> dict[str, bool]:
        with anyio.CancelScope(shield=True):
            await asyncio.sleep(0)
        return {"ok": True}

    async def _main() -> int:
        configure_event_loop()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return (await client.get("/test/cancel-scope")).status_code

    assert uvloop.run(_main()) == 200


async def test_tasks_start_lazily() -> None:
    """A factory left by an earlier setup is dropped: asyncio's default."""
    loop = asyncio.get_running_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    steps: list[str] = []

    async def _child() -> None:
        steps.append("child started")

    try:
        configure_event_loop()
        task = asyncio.create_task(_child())
        steps.append("created")
        await task
    finally:
        loop.set_task_factory(None)

    assert steps == ["created", "child started"]
