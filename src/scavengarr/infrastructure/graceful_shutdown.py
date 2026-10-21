"""Graceful shutdown helper: track in-flight requests and drain on stop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from scavengarr.domain.ports.telemetry import RouteClass

log = structlog.get_logger(__name__)


@dataclass(eq=False, slots=True)
class WatchedRequest:
    """A request in flight for the stuck-request check: the task that runs
    its route, its route class, its start on the loop's clock and its id."""

    task: asyncio.Task[object]
    route: RouteClass
    started: float
    request_id: str | None = None
    # Logged once: set by the check that logged it
    reported: bool = False


class GracefulShutdown:
    """Track active requests and wait for them to drain before shutdown.

    Usage::

        gs = GracefulShutdown()

        # In middleware:
        gs.request_started()
        try:
            ...
        finally:
            gs.request_finished()

        # In lifespan finally:
        await gs.wait_for_drain(timeout=10.0)
    """

    def __init__(self) -> None:
        self._active = 0
        self._shutting_down = False
        self._drained = asyncio.Event()
        self._drained.set()  # starts drained (0 active)
        self._ready = False
        self._watched: set[WatchedRequest] = set()

    @property
    def active_requests(self) -> int:
        return self._active

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def is_ready(self) -> bool:
        """True once the application has finished startup."""
        return self._ready and not self._shutting_down

    def mark_ready(self) -> None:
        """Signal that startup is complete and the app is ready."""
        self._ready = True

    def request_started(self) -> None:
        self._active += 1
        self._drained.clear()

    def request_finished(self) -> None:
        self._active -= 1
        if self._active <= 0:
            self._active = 0
            self._drained.set()

    @property
    def watched_requests(self) -> tuple[WatchedRequest, ...]:
        """The requests in flight that the stuck-request check reads."""
        return tuple(self._watched)

    def watch_request(self, request: WatchedRequest) -> None:
        self._watched.add(request)

    def forget_request(self, request: WatchedRequest) -> None:
        self._watched.discard(request)

    async def wait_for_drain(self, *, timeout: float = 10.0) -> None:
        """Wait for all active requests to finish, up to *timeout* seconds."""
        self._shutting_down = True
        if self._active == 0:
            return
        log.info("graceful_shutdown_draining", active_requests=self._active)
        try:
            await asyncio.wait_for(self._drained.wait(), timeout=timeout)
            log.info("graceful_shutdown_drained")
        except TimeoutError:
            log.warning(
                "graceful_shutdown_timeout",
                remaining_requests=self._active,
                timeout=timeout,
            )
