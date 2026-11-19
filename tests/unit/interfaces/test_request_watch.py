"""Tests for the request watch (OpenSpec add-stall-watchdog, group 3): the
route classes, and the middleware that notes each request's task for the
stuck-request check."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from scavengarr.domain.ports.telemetry import RouteClass
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.graceful_shutdown import (
    GracefulShutdown,
    WatchedRequest,
)
from scavengarr.infrastructure.telemetry.watchdog import await_stack
from scavengarr.interfaces.api.middleware import RequestWatchMiddleware, route_class
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

from ..waiting import eventually


@pytest.mark.parametrize(
    ("path", "route"),
    [
        ("/api/v1/stremio/stream/movie/tt0000001.json", "stremio_stream"),
        ("/api/v1/stremio/catalog/movie/trending.json", "stremio_catalog"),
        ("/api/v1/torznab/kinoger", "torznab"),
        ("/api/v1/torznab/indexers", "torznab"),
        ("/api/v1/download/0b8f/info", "torznab"),
        ("/api/v1/stremio/manifest.json", "other"),
        ("/api/v1/stats/metrics", "other"),
        ("/api/v1/stremio/play/abc", None),
        ("/api/v1/stremio/proxy/abc/seg-1.ts", None),
    ],
)
def test_the_route_class_of_a_path(path: str, route: RouteClass | None) -> None:
    assert route_class(path) == route


class _Route:
    """A route that tells when it runs and waits until it is released."""

    def __init__(self) -> None:
        self.running = asyncio.Event()
        self.release = asyncio.Event()

    async def endpoint(self, request: Request) -> JSONResponse:
        return await self._waits()

    async def _waits(self) -> JSONResponse:
        self.running.set()
        await self.release.wait()
        return JSONResponse({})


@pytest.fixture
async def watched() -> AsyncIterator[
    tuple[GracefulShutdown, _Route, httpx.AsyncClient]
]:
    shutdown = GracefulShutdown()
    route = _Route()
    app = Starlette(
        routes=[
            Route("/api/v1/torznab/x", route.endpoint),
            Route("/api/v1/stremio/proxy/{sid}/{path:path}", route.endpoint),
        ]
    )
    app.add_middleware(RequestWatchMiddleware, shutdown=shutdown)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield shutdown, route, client
    route.release.set()


class TestMiddleware:
    async def test_a_request_is_watched_while_it_runs(
        self, watched: tuple[GracefulShutdown, _Route, httpx.AsyncClient]
    ) -> None:
        shutdown, route, client = watched
        tokens = structlog.contextvars.bind_contextvars(request_id="ab12")
        try:
            pending = asyncio.create_task(client.get("/api/v1/torznab/x"))
        finally:
            structlog.contextvars.reset_contextvars(**tokens)
        await asyncio.wait_for(route.running.wait(), 2)

        [request] = shutdown.watched_requests
        assert request.route == "torznab"
        assert request.request_id == "ab12"
        assert "in _waits" in await_stack(request.task)
        route.release.set()
        assert (await pending).status_code == 200
        assert shutdown.watched_requests == ()

    async def test_a_cancelled_request_is_forgotten(
        self, watched: tuple[GracefulShutdown, _Route, httpx.AsyncClient]
    ) -> None:
        """A client that disconnects ends its request with a cancellation."""
        shutdown, route, client = watched
        pending = asyncio.create_task(client.get("/api/v1/torznab/x"))
        await asyncio.wait_for(route.running.wait(), 2)

        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
        assert shutdown.watched_requests == ()

    async def test_a_playback_is_never_watched(
        self, watched: tuple[GracefulShutdown, _Route, httpx.AsyncClient]
    ) -> None:
        """A proxied playback runs as long as the film."""
        shutdown, route, client = watched
        pending = asyncio.create_task(client.get("/api/v1/stremio/proxy/a/seg-1.ts"))
        await asyncio.wait_for(route.running.wait(), 2)

        assert shutdown.watched_requests == ()
        route.release.set()
        assert (await pending).status_code == 200

    async def test_the_lifespan_passes_through_unwatched(self) -> None:
        """The lifespan runs as long as the app: watched, it would be named
        stuck after 5 min. Read while it runs: its end forgets it either way."""
        shutdown = GracefulShutdown()
        seen: list[tuple[WatchedRequest, ...]] = []

        @contextlib.asynccontextmanager
        async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
            seen.append(shutdown.watched_requests)
            yield
            seen.append(shutdown.watched_requests)

        app = Starlette(lifespan=_lifespan)
        app.add_middleware(RequestWatchMiddleware, shutdown=shutdown)
        messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[str] = []

        async def _receive() -> dict[str, Any]:
            return messages.pop(0)

        async def _send(message: dict[str, Any]) -> None:
            sent.append(message["type"])

        scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
        await app(scope, _receive, _send)

        assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert seen == [(), ()]


async def test_the_watched_task_runs_the_route_under_the_apps_middleware(
    tmp_path: Path,
) -> None:
    """Innermost: ``log_requests`` and the rate limit run the app below them
    in a task of their own, so a watch outside them would note a task that
    waits in ``call_next``."""
    config = load_config(
        cli_overrides={"plugin_dir": tmp_path, "cache_dir": tmp_path / "cache"}
    )
    assert config.api_rate_limit_rpm > 0
    app = create_app(config)
    shutdown = cast(AppState, app.state).graceful_shutdown
    release = asyncio.Event()

    async def _route_waits() -> dict[str, str]:
        await release.wait()
        return {}

    app.add_api_route("/test/waits", _route_waits)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        pending = asyncio.create_task(client.get("/test/waits"))
        await eventually(lambda: bool(shutdown.watched_requests))
        [request] = shutdown.watched_requests
        stack = await_stack(request.task)
        release.set()
        assert (await pending).status_code == 200

    assert request.route == "other"
    # log_requests runs first: the stuck line names the request's id
    assert request.request_id is not None
    assert request.task is not pending
    assert "in _route_waits" in stack
    assert "in call_next" not in stack
    assert shutdown.watched_requests == ()
