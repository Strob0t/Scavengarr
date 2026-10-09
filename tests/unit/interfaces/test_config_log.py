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
                "sources": {
                    "stremio.max_concurrent_plugins": "yaml",
                    "tmdb_api_key": "yaml",
                },
                "stremio.max_concurrent_plugins": 15,
                "tmdb_api_key": "***",
            }
        ]

    def test_sources_name_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_STREMIO_MAX_CONCURRENT_PLUGINS", "9")

        logs = _logs(tmp_path, {"stremio": {"max_concurrent_plugins": 15}})

        assert logs[0]["sources"] == {"stremio.max_concurrent_plugins": "env"}
        assert logs[0]["stremio.max_concurrent_plugins"] == 9

    def test_unknown_env_names_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECOND", "20")

        logs = _logs(tmp_path, {})

        assert logs[1] == {
            "event": "config_unknown_env",
            "log_level": "warning",
            "names": ["SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECOND"],
        }

    def test_disagreeing_env_forms_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("SCAVENGARR_LOGGING_LEVEL", "ERROR")

        logs = _logs(tmp_path, {})

        assert logs[1] == {
            "event": "config_env_conflict",
            "log_level": "warning",
            "used": "SCAVENGARR_LOG_LEVEL",
            "ignored": "SCAVENGARR_LOGGING_LEVEL",
        }

    def test_unknown_keys_warn(self, tmp_path: Path) -> None:
        logs = _logs(tmp_path, {"stremio": {"probe_at_stream_time": True}})

        assert logs[1] == {
            "event": "config_unknown_keys",
            "log_level": "warning",
            "config_file": str(tmp_path / "config.yaml"),
            "keys": ["stremio.probe_at_stream_time"],
        }

    def test_the_removed_year_penalty_loads(self, tmp_path: Path) -> None:
        """``stremio.title_year_penalty`` is gone (the year decides, series
        identity); a YAML that still sets it loads, the key reported."""
        logs = _logs(tmp_path, {"stremio": {"title_year_penalty": 0.3}})

        assert logs[1]["event"] == "config_unknown_keys"
        assert logs[1]["keys"] == ["stremio.title_year_penalty"]

    def test_without_a_file(self) -> None:
        with capture_logs() as logs:
            _log_config(load_config())

        assert logs == [
            {
                "event": "config_effective",
                "log_level": "info",
                "config_file": None,
                "sources": {},
            }
        ]
