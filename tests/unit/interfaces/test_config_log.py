"""Tests for the startup lines that tell what configuration the server runs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from structlog.testing import capture_logs
from structlog.typing import EventDict

from scavengarr.infrastructure.config import load_config
from scavengarr.interfaces.composition import _log_config


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCAVENGARR_* values of the shell would count as changed values."""
    for name in list(os.environ):
        if name.upper().startswith("SCAVENGARR_"):
            monkeypatch.delenv(name)


def _logs(tmp_path: Path, data: dict[str, object]) -> list[EventDict]:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    config = load_config(config_path=path)
    with capture_logs() as logs:
        _log_config(config)
    return logs


class TestLogConfig:
    def test_effective_values_name_the_file_and_the_changes(
        self, tmp_path: Path
    ) -> None:
        logs = _logs(
            tmp_path,
            {"stremio": {"max_concurrent_plugins": 15}, "tmdb_api_key": "abc123"},
        )

        assert logs == [
            {
                "event": "config_effective",
                "log_level": "info",
                "config_file": str(tmp_path / "config.yaml"),
                "stremio.max_concurrent_plugins": 15,
                "tmdb_api_key": "***",
            }
        ]

    def test_unknown_keys_warn(self, tmp_path: Path) -> None:
        logs = _logs(tmp_path, {"stremio": {"probe_at_stream_time": True}})

        assert logs[1] == {
            "event": "config_unknown_keys",
            "log_level": "warning",
            "config_file": str(tmp_path / "config.yaml"),
            "keys": ["stremio.probe_at_stream_time"],
        }

    def test_without_a_file(self) -> None:
        with capture_logs() as logs:
            _log_config(load_config())

        assert logs == [
            {"event": "config_effective", "log_level": "info", "config_file": None}
        ]
