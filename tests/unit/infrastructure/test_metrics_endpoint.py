"""Tests for /api/v1/stats/metrics endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.graceful_shutdown import GracefulShutdown
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.hoster_resolvers.state_store import HosterStateStore
from scavengarr.infrastructure.plugins.history import PluginHistory
from scavengarr.infrastructure.telemetry import Telemetry


def _build_app() -> TestClient:
    """Build a minimal FastAPI app with the stats router for testing."""
    from fastapi import FastAPI

    from scavengarr.interfaces.api.stats.router import router
    from scavengarr.interfaces.app_state import AppState

    app = FastAPI()
    app.state = AppState()
    app.state.telemetry = Telemetry()
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

    def test_contains_event_loop_lag(self) -> None:
        client = _build_app()
        data = client.get("/api/v1/stats/metrics").json()
        assert data["event_loop"] == {"samples": 0}

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
        telemetry = app.state.telemetry
        with telemetry.stage("plugin_search", plugin="test-plugin") as stage:
            stage.outcome = "hits"
        telemetry.record("plugin_results", 5, plugin="test-plugin")
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

    def test_unresolved_hosters_most_frequent_first(self) -> None:
        client = _build_app()
        registry = MagicMock()
        registry.unresolved_hosts.return_value = {"byse": 7, "other": 2}
        client.app.state.hoster_resolver_registry = registry

        data = client.get("/api/v1/stats/metrics").json()

        assert list(data["unresolved_hosters"].items()) == [("byse", 7), ("other", 2)]

    def test_the_plugin_record_needs_the_wiring(self) -> None:
        client = _build_app()

        resp = client.get("/api/v1/stats/plugins")

        assert resp.status_code == 503
        assert resp.json() == {"error": "plugin_history_not_enabled"}

    def test_the_plugin_record(self) -> None:
        """The long-term record per plugin and day (ideas backlog, N2)."""
        client = _build_app()
        history = PluginHistory(AsyncMock())
        history.count("sto", "results", 3)
        history.count("sto", "checks")
        client.app.state.plugin_history = history

        data = client.get("/api/v1/stats/plugins").json()

        sto = data["plugins"]["sto"]
        assert sto["last_result_day"] == data["today"]
        assert sto["unreachable_share"] == {"30": 0.0, "90": 0.0, "180": 0.0}
        assert sto["days"] == {data["today"]: {"results": 3, "checks": 1}}

    async def test_the_restored_resolver_state(self) -> None:
        """What the start restored (openspec persist-resolver-state)."""
        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)
        store = HosterStateStore(cache, HosterResolverRegistry(), {})
        await store.restore()
        client = _build_app()
        client.app.state.hoster_state_store = store

        data = client.get("/api/v1/stats/metrics").json()

        assert data["hoster_state"]["resolutions"] == 0
        assert data["hoster_state"]["restored_at"] is not None
