"""Tests for the CLI entry point (host/port resolution)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scavengarr.interfaces.cli import __main__ as cli


@pytest.fixture()
def run_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Isolate os.environ and stub out app creation and uvicorn."""
    env = {k: v for k, v in os.environ.items() if k not in {"HOST", "PORT"}}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(cli, "create_app", MagicMock())
    monkeypatch.setattr(cli, "configure_logging", MagicMock())
    run = MagicMock()
    monkeypatch.setattr(cli.uvicorn, "run", run)
    return run


def test_defaults_to_all_interfaces_port_7979(run_mock: MagicMock) -> None:
    cli.start([])

    assert run_mock.call_args.kwargs["host"] == "0.0.0.0"
    assert run_mock.call_args.kwargs["port"] == 7979


def test_host_and_port_from_dotenv(run_mock: MagicMock, tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("HOST=127.0.0.1\nPORT=9999\n", encoding="utf-8")

    cli.start(["--dotenv", str(dotenv)])

    assert run_mock.call_args.kwargs["host"] == "127.0.0.1"
    assert run_mock.call_args.kwargs["port"] == 9999


def test_cli_flags_beat_dotenv(run_mock: MagicMock, tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("HOST=127.0.0.1\nPORT=9999\n", encoding="utf-8")

    cli.start(["--dotenv", str(dotenv), "--host", "10.0.0.1", "--port", "8000"])

    assert run_mock.call_args.kwargs["host"] == "10.0.0.1"
    assert run_mock.call_args.kwargs["port"] == 8000


def test_uvicorn_access_log_is_off(run_mock: MagicMock) -> None:
    # The app's http_request line is the access log, its query masked;
    # uvicorn's own line repeated the request with the CDN's tokens
    cli.start([])

    assert run_mock.call_args.kwargs["access_log"] is False
