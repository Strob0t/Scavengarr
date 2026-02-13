"""FastAPI middleware for API rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = structlog.get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter per client IP.

    Args:
        app: ASGI application.
        requests_per_minute: Max requests per IP per minute. 0 = unlimited.
    """

    def __init__(self, app: object, requests_per_minute: int = 120) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._rpm = requests_per_minute
        self._window: dict[str, list[float]] = defaultdict(list)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self._rpm <= 0:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - 60.0

        # Prune old entries
        timestamps = self._window[client_ip]
        self._window[client_ip] = [t for t in timestamps if t > cutoff]

        if len(self._window[client_ip]) >= self._rpm:
            log.warning(
                "rate_limit_exceeded",
                client_ip=client_ip,
                rpm=self._rpm,
                current=len(self._window[client_ip]),
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )

        self._window[client_ip].append(now)

        response = await call_next(request)
        remaining = max(0, self._rpm - len(self._window[client_ip]))
        response.headers["X-RateLimit-Limit"] = str(self._rpm)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
