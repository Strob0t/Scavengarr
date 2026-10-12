"""The liveness route reports which build runs."""

from __future__ import annotations

import pytest
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
