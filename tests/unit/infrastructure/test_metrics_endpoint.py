"""Tests for /api/v1/stats/metrics endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.graceful_shutdown import GracefulShutdown
from scavengarr.infrastructure.metrics import MetricsCollector


def _build_app() -> TestClient:
    """Build a minimal FastAPI app with the stats router for testing."""
    from fastapi import FastAPI

    from scavengarr.interfaces.api.stats.router import router
    from scavengarr.interfaces.app_state import AppState

    app = FastAPI()
    app.state = AppState()
    app.state.metrics = MetricsCollector()
    app.state.circuit_breaker = PluginCircuitBreaker()
    app.state.concurrency_pool = ConcurrencyPool(httpx_slots=10, pw_slots=3)
    app.state.graceful_shutdown = GracefulShutdown()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


class TestMetricsEndpoint:
    def test_returns_200(self) -> None:
        client = _build_app()
        resp = client.get("/api/v1/stats/metrics")
        assert resp.status_code == 200

    def test_contains_uptime(self) -> None:
        client = _build_app()
        data = client.get("/api/v1/stats/metrics").json()
        assert "uptime_seconds" in data

    def test_contains_plugins(self) -> None:
        client = _build_app()
        data = client.get("/api/v1/stats/metrics").json()
        assert "plugins" in data

    def test_contains_circuit_breaker(self) -> None:
        client = _build_app()
        data = client.get("/api/v1/stats/metrics").json()
        assert "circuit_breaker" in data

    def test_contains_concurrency_pool(self) -> None:
        client = _build_app()
        data = client.get("/api/v1/stats/metrics").json()
        pool = data["concurrency_pool"]
        assert pool["httpx_slots"] == 10
        assert pool["pw_slots"] == 3
        assert pool["httpx_available"] == 10
        assert pool["pw_available"] == 3
        assert pool["active_requests"] == 0

    def test_contains_shutdown(self) -> None:
        client = _build_app()
        data = client.get("/api/v1/stats/metrics").json()
        shutdown = data["shutdown"]
        assert shutdown["is_ready"] is False
        assert shutdown["is_shutting_down"] is False
        assert shutdown["active_requests"] == 0

    def test_plugin_search_reflected(self) -> None:
        client = _build_app()
        # Record a search
        app = client.app
        app.state.metrics.record_plugin_search(
            "test-plugin", 1_000_000, 5, success=True
        )
        data = client.get("/api/v1/stats/metrics").json()
        assert "test-plugin" in data["plugins"]
        assert data["plugins"]["test-plugin"]["searches"] == 1
        assert data["plugins"]["test-plugin"]["total_results"] == 5

    def test_circuit_breaker_state_reflected(self) -> None:
        client = _build_app()
        cb = client.app.state.circuit_breaker
        for _ in range(5):
            cb.record_failure("flaky")
        data = client.get("/api/v1/stats/metrics").json()
        assert data["circuit_breaker"]["flaky"]["state"] == "open"
