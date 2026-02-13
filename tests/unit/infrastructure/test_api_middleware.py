"""Tests for RateLimitMiddleware."""

from __future__ import annotations

import pytest
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
