"""The health routes: liveness names the build, readiness the startup and
the network."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.infrastructure.config import AppConfig
from scavengarr.infrastructure.version import APP_VERSION
from scavengarr.interfaces.app import create_app


def test_liveness_reports_version_commit_and_build_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCAVENGARR_COMMIT", "7eeb50b63a57")
    monkeypatch.setenv("SCAVENGARR_BUILT", "2026-10-06T18:00:00Z")

    data = TestClient(create_app(AppConfig())).get("/api/v1/healthz").json()

    assert data["status"] == "ok"
    assert (data["version"], data["commit"], data["built"]) == (
        APP_VERSION,
        "7eeb50b63a57",
        "2026-10-06T18:00:00Z",
    )


class _Probe:
    """The network probe's verdict, fixed (``NetworkProbe.lost``)."""

    def __init__(self, *, lost: bool) -> None:
        self._lost = lost
        self.asked = 0

    async def lost(self) -> bool:
        self.asked += 1
        return self._lost


def _started_app(probe: _Probe) -> FastAPI:
    app = create_app(AppConfig())
    app.state.network_probe = probe
    app.state.graceful_shutdown.mark_ready()
    return app


def test_readiness_waits_for_the_startup() -> None:
    probe = _Probe(lost=False)
    app = create_app(AppConfig())
    app.state.network_probe = probe

    resp = TestClient(app).get("/api/v1/readyz")

    assert (resp.status_code, resp.json()) == (503, {"status": "not_ready"})
    assert probe.asked == 0


def test_a_started_app_with_its_network_is_ready() -> None:
    resp = TestClient(_started_app(_Probe(lost=False))).get("/api/v1/readyz")

    assert (resp.status_code, resp.json()) == (200, {"status": "ready"})


def test_a_lost_network_makes_the_app_unready_but_not_dead() -> None:
    """Readiness tells the lost network (row 125); liveness stays 200."""
    client = TestClient(_started_app(_Probe(lost=True)))

    ready = client.get("/api/v1/readyz")

    assert (ready.status_code, ready.json()) == (
        503,
        {"status": "not_ready", "reason": "network"},
    )
    assert client.get("/api/v1/healthz").status_code == 200
