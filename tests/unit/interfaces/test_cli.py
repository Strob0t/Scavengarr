"""Tests for the CLI entry point (host/port resolution)."""

from __future__ import annotations

import os
import signal
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
    monkeypatch.setattr(cli.signal, "signal", MagicMock())
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


def test_uvicorn_bounds_the_graceful_shutdown(run_mock: MagicMock) -> None:
    # An open proxied playback would hold the drain past Docker's 10 s stop
    # grace; after 5 s uvicorn cancels it and the lifespan shutdown still runs
    cli.start([])

    assert run_mock.call_args.kwargs["timeout_graceful_shutdown"] == 5


def test_sigterm_writes_the_queued_log_lines_first(
    run_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Uvicorn raises the stop signal again after its shutdown: the default
    # handler killed the process before the listener thread wrote the last
    # queued lines; the process still ends from the signal
    calls = MagicMock()
    monkeypatch.setattr(cli, "stop_async_logging", calls.stop)
    monkeypatch.setattr(cli.signal, "signal", calls.signal)
    monkeypatch.setattr(cli.signal, "raise_signal", calls.raise_signal)
    cli.start([])
    [(sig, handler)] = [c.args for c in calls.signal.call_args_list]
    assert sig == signal.SIGTERM

    handler(signal.SIGTERM, None)

    assert [c[0] for c in calls.mock_calls[1:]] == ["stop", "signal", "raise_signal"]
    assert calls.signal.call_args.args == (signal.SIGTERM, signal.SIG_DFL)
    calls.raise_signal.assert_called_once_with(signal.SIGTERM)
