"""Tests for RateLimitMiddleware."""

from __future__ import annotations

import time
from collections import deque

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from scavengarr.interfaces.api.middleware import RateLimitMiddleware


async def _hello(request: Request) -> JSONResponse:
    return JSONResponse({"msg": "ok"})


def _create_app(rpm: int = 5) -> Starlette:
    app = Starlette(routes=[Route("/", _hello)])
    app.add_middleware(RateLimitMiddleware, requests_per_minute=rpm)
    return app


class TestRateLimitExemptions:
    def test_hls_proxy_and_health_are_not_counted(self) -> None:
        """A playing stream loads a segment every few seconds (dozens at
        start); health probes come from the orchestrator."""
        app = Starlette(
            routes=[
                Route("/", _hello),
                Route("/api/v1/stremio/proxy/{sid}/{path:path}", _hello),
                Route("/api/v1/healthz", _hello),
            ]
        )
        app.add_middleware(RateLimitMiddleware, requests_per_minute=2)
        client = TestClient(app)

        for _ in range(10):
            assert client.get("/api/v1/stremio/proxy/abc/seg1.ts").status_code == 200
            assert client.get("/api/v1/healthz").status_code == 200

        # The budget of the counted endpoints is untouched
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 429

    def test_idle_clients_are_forgotten(self) -> None:
        # Only the same client's next request pruned its deque, so the
        # entry of a client that never came back used to stay forever
        mw = RateLimitMiddleware(Starlette(), requests_per_minute=5)
        now = time.monotonic()
        mw._window = {"idle": deque([now - 120]), "active": deque([now - 5])}

        mw._evict_idle(now - 60)

        assert list(mw._window) == ["active"]


class TestRateLimitMiddleware:
    def test_allows_requests_under_limit(self) -> None:
        app = _create_app(rpm=10)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert resp.headers["X-RateLimit-Limit"] == "10"

    def test_blocks_requests_over_limit(self) -> None:
        app = _create_app(rpm=3)
        client = TestClient(app)
        for _ in range(3):
            resp = client.get("/")
            assert resp.status_code == 200

        # 4th request should be blocked
        resp = client.get("/")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        body = resp.json()
        assert "Rate limit exceeded" in body["error"]

    def test_unlimited_when_rpm_zero(self) -> None:
        app = _create_app(rpm=0)
        client = TestClient(app)
        # Should always pass
        for _ in range(20):
            resp = client.get("/")
            assert resp.status_code == 200

    def test_remaining_header_decreases(self) -> None:
        app = _create_app(rpm=5)
        client = TestClient(app)

        resp1 = client.get("/")
        remaining1 = int(resp1.headers["X-RateLimit-Remaining"])

        resp2 = client.get("/")
        remaining2 = int(resp2.headers["X-RateLimit-Remaining"])

        assert remaining2 < remaining1
