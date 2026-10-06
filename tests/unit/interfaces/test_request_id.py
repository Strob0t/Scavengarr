"""Tests for the request id in the log context and the response header."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.infrastructure.config import AppConfig
from scavengarr.interfaces.app import create_app


def _app() -> FastAPI:
    app = create_app(AppConfig())

    @app.get("/test/context")
    async def context() -> dict[str, str | None]:
        async def _task() -> str | None:
            return structlog.contextvars.get_contextvars().get("request_id")

        # A task the request starts (a shared search) logs with its id
        spawned = await asyncio.create_task(_task())
        own = structlog.contextvars.get_contextvars().get("request_id")
        return {"own": own, "spawned": spawned}

    return app


class TestRequestId:
    def test_the_request_and_its_tasks_log_with_the_id_of_the_header(self) -> None:
        resp = TestClient(_app()).get("/test/context")

        request_id = resp.headers["X-Request-ID"]
        assert len(request_id) == 12
        assert resp.json() == {"own": request_id, "spawned": request_id}

    def test_each_request_gets_its_own_id(self) -> None:
        client = TestClient(_app())

        first = client.get("/test/context").headers["X-Request-ID"]
        second = client.get("/test/context").headers["X-Request-ID"]

        assert first != second

    def test_an_incoming_id_is_not_taken(self) -> None:
        resp = TestClient(_app()).get(
            "/test/context", headers={"X-Request-ID": "forged\nline"}
        )

        assert resp.headers["X-Request-ID"] != "forged\nline"
        assert resp.json()["own"] == resp.headers["X-Request-ID"]

    def test_the_id_is_unbound_after_the_request(self) -> None:
        TestClient(_app()).get("/test/context")

        assert "request_id" not in structlog.contextvars.get_contextvars()
