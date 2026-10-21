"""FastAPI middleware: the API's rate limit and the request watch."""

from __future__ import annotations

import asyncio
import time
from collections import deque

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from scavengarr.domain.ports.telemetry import RouteClass
from scavengarr.infrastructure.graceful_shutdown import (
    GracefulShutdown,
    WatchedRequest,
)

log = structlog.get_logger(__name__)

# How many dispatch cycles between full sweeps of stale client entries.
_GC_INTERVAL = 256

# Not counted: the requests of a playing stream (HLS manifests and segments
# every few seconds, dozens at playback start; /play again for each seek),
# a 429 would end its playback, and health probes
_EXEMPT_PREFIXES = (
    "/api/v1/stremio/proxy/",
    "/api/v1/stremio/play/",
    "/api/v1/stremio/health",
    "/api/v1/healthz",
    "/api/v1/readyz",
)

# The stuck-request check's route classes by path prefix (the first match);
# None: never watched
_ROUTE_CLASSES: tuple[tuple[str, RouteClass | None], ...] = (
    ("/api/v1/stremio/stream/", "stremio_stream"),
    ("/api/v1/stremio/catalog/", "stremio_catalog"),
    ("/api/v1/stremio/play/", None),
    ("/api/v1/stremio/proxy/", None),
    ("/api/v1/torznab/", "torznab"),
    ("/api/v1/download/", "torznab"),
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP.

    Uses a deque per IP for O(1) append and efficient left-pruning.
    Periodically evicts IPs with no recent requests to prevent unbounded
    memory growth.

    Args:
        app: ASGI application.
        requests_per_minute: Max requests per IP per minute. 0 = unlimited.
    """

    def __init__(self, app: object, requests_per_minute: int = 120) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._rpm = requests_per_minute
        self._window: dict[str, deque[float]] = {}
        self._dispatch_count = 0

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._rpm <= 0 or request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - 60.0

        # Get or create deque for this client
        timestamps = self._window.get(client_ip)
        if timestamps is None:
            timestamps = deque()
            self._window[client_ip] = timestamps

        # Prune expired entries from the left (oldest first) — O(k) where k = expired
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        if len(timestamps) >= self._rpm:
            log.warning(
                "rate_limit_exceeded",
                client_ip=client_ip,
                rpm=self._rpm,
                current=len(timestamps),
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )

        timestamps.append(now)

        # Periodic GC of clients without a request inside the window
        self._dispatch_count += 1
        if self._dispatch_count >= _GC_INTERVAL:
            self._dispatch_count = 0
            self._evict_idle(cutoff)

        response = await call_next(request)
        remaining = max(0, self._rpm - len(timestamps))
        response.headers["X-RateLimit-Limit"] = str(self._rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    def _evict_idle(self, cutoff: float) -> None:
        """Forget clients whose last request is older than *cutoff*.

        A deque is only pruned on its client's next request, so a client
        that never comes back keeps a non-empty deque.
        """
        idle = [ip for ip, dq in self._window.items() if not dq or dq[-1] <= cutoff]
        for ip in idle:
            del self._window[ip]


def route_class(path: str) -> RouteClass | None:
    """The stuck-request check's route class of *path*; None for play and
    proxy requests, which last as long as a playback and are never watched."""
    for prefix, route in _ROUTE_CLASSES:
        if path.startswith(prefix):
            return route
    return "other"


class RequestWatchMiddleware:
    """Notes each HTTP request's task, route class and start in
    ``GracefulShutdown`` for the stuck-request check, and forgets it when the
    request ends, cancelled or not.

    Pure ASGI and the innermost middleware: ``log_requests`` and the rate
    limit (``BaseHTTPMiddleware``) run the app below them in a task of their
    own, so the task noted here is the one that runs the route.
    """

    def __init__(self, app: ASGIApp, shutdown: GracefulShutdown) -> None:
        self._app = app
        self._shutdown = shutdown

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        route = route_class(scope["path"]) if scope["type"] == "http" else None
        task = asyncio.current_task()
        if route is None or task is None:
            await self._app(scope, receive, send)
            return
        request = WatchedRequest(
            task=task,
            route=route,
            started=asyncio.get_running_loop().time(),
            request_id=structlog.contextvars.get_contextvars().get("request_id"),
        )
        self._shutdown.watch_request(request)
        try:
            await self._app(scope, receive, send)
        finally:
            self._shutdown.forget_request(request)
