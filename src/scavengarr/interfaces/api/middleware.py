"""FastAPI middleware for API rate limiting."""

from __future__ import annotations

import time
from collections import deque

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = structlog.get_logger(__name__)

# How many dispatch cycles between full sweeps of stale client entries.
_GC_INTERVAL = 256


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
        if self._rpm <= 0:
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

        # Periodic GC: evict IPs whose deques are empty
        self._dispatch_count += 1
        if self._dispatch_count >= _GC_INTERVAL:
            self._dispatch_count = 0
            stale = [ip for ip, dq in self._window.items() if not dq]
            for ip in stale:
                del self._window[ip]

        response = await call_next(request)
        remaining = max(0, self._rpm - len(timestamps))
        response.headers["X-RateLimit-Limit"] = str(self._rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
