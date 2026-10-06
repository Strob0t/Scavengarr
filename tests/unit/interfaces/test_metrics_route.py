"""Tests for the Prometheus endpoint ``GET /metrics``."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.telemetry import CONTENT_TYPE, Telemetry
from scavengarr.interfaces.app import create_app


def test_metrics_in_the_prometheus_text_format() -> None:
    app = create_app(AppConfig())
    app.state.telemetry = Telemetry()
    app.state.telemetry.count("plugin_search", "skipped", plugin="kinoger")

    resp = TestClient(app).get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == CONTENT_TYPE
    line = 'scavengarr_plugin_search_total{outcome="skipped",plugin="kinoger"} 1.0'
    assert line in resp.text
    assert "process_cpu_seconds_total" in resp.text
