"""Tests for scripts/portainer.py (no live Portainer)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_PORTAINER = "http://portainer.test:9000"
_DOCKER = f"{_PORTAINER}/api/endpoints/3/docker"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        return importlib.import_module("portainer")
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()


def _frame(text: str, stream: int = 1) -> bytes:
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def _docker_endpoint() -> respx.Route:
    respx.get(f"{_PORTAINER}/api/endpoints").respond(json=[{"Id": 3, "Type": 1}])
    return respx.get(f"{_DOCKER}/containers/json").respond(json=[])


class TestMask:
    def test_urls_keep_only_scheme_and_host(self) -> None:
        line = "GET https://cdn.example.net/hls/t0k3n/index.m3u8?i=10.1.2.3&e=99 ok"
        assert _mod.mask(line) == "GET https://cdn.example.net/… ok"

    def test_a_url_without_path_stays_whole(self) -> None:
        assert (
            _mod.mask("HEAD https://kinoger.com 200") == "HEAD https://kinoger.com 200"
        )

    def test_credentials_in_a_url_are_redacted(self) -> None:
        masked = _mod.mask("redis_url=redis://:hunter2@redis:6379/0")
        assert "hunter2" not in masked
        assert masked == "redis_url=redis://<redacted>@redis:6379/…"

    def test_percent_encoded_urls_are_dropped(self) -> None:
        masked = _mod.mask("next=https%3A%2F%2Fcdn.example.net%2Fx%3Ft%3Dsecret end")
        assert masked == "next=<url> end"

    @pytest.mark.parametrize(
        "address", ["172.18.0.1", "10.0.0.254", "2001:db8:abcd:1::5", "fe80::1"]
    )
    def test_ip_addresses_are_replaced(self, address: str) -> None:
        assert _mod.mask(f'"client_host": "{address}"') == '"client_host": "<ip>"'

    def test_times_and_versions_stay(self) -> None:
        line = "2026-10-06T11:09:08.998Z basedpyright 1.40.1 at 11:09:08"
        assert _mod.mask(line) == line

    @pytest.mark.parametrize(
        ("line", "secret"),
        [
            ('{"api_key": "abc123"}', "abc123"),
            ("/api?t=search&apikey=xyz789", "xyz789"),
            ("Authorization: Bearer tok42", "tok42"),
            ("password=pa55word", "pa55word"),
        ],
    )
    def test_secret_values_are_redacted(self, line: str, secret: str) -> None:
        masked = _mod.mask(line)
        assert secret not in masked
        assert "<redacted>" in masked


class TestCredentials:
    def test_the_env_file_wins_over_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env.devcontainer"
        env_file.write_text(
            "OTHER=1\nPORTAINER_URL=http://pt.test:9000\nPORTAINER_API_KEY='fresh'\n"
        )
        monkeypatch.setenv("PORTAINER_API_KEY", "stale")

        assert _mod.credentials(env_file) == ("http://pt.test:9000", "fresh")

    def test_the_environment_fills_what_the_file_lacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env.devcontainer"
        env_file.write_text("PORTAINER_API_KEY=key\n")
        monkeypatch.setenv("PORTAINER_URL", "http://env.test:9000")

        assert _mod.credentials(env_file) == ("http://env.test:9000", "key")

    def test_missing_credentials_stop_without_printing_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PORTAINER_URL", raising=False)
        monkeypatch.delenv("PORTAINER_API_KEY", raising=False)

        with pytest.raises(SystemExit, match="PORTAINER_URL and PORTAINER_API_KEY"):
            _mod.credentials(tmp_path / "missing")


class TestRequestBudget:
    def test_requests_under_the_limit_do_not_wait(self, tmp_path: Path) -> None:
        sleeps: list[float] = []
        budget = _mod.RequestBudget(
            tmp_path / "b.json", limit=3, clock=lambda: 100.0, sleep=sleeps.append
        )
        for _ in range(3):
            budget.acquire()
        assert sleeps == []

    def test_a_full_budget_waits_for_the_oldest_request(self, tmp_path: Path) -> None:
        now = [100.0]
        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        state = tmp_path / "b.json"
        first = _mod.RequestBudget(state, limit=2, window=60, clock=lambda: now[0])
        first.acquire()
        now[0] = 110.0
        first.acquire()
        # Another process sharing the state file
        second = _mod.RequestBudget(
            state, limit=2, window=60, clock=lambda: now[0], sleep=_sleep
        )
        second.acquire()

        assert sleeps == [pytest.approx(50.0)]
        assert len(json.loads(state.read_text())) == 2


class TestDemux:
    def test_joins_stdout_and_stderr_frames(self) -> None:
        raw = _frame("one\n") + _frame("two\n", stream=2)
        assert _mod.demux(raw) == "one\ntwo\n"

    def test_plain_text_passes_through(self) -> None:
        assert _mod.demux(b"plain log line\n") == "plain log line\n"


class TestPortainer:
    def _client(self, **kwargs: Any) -> Any:
        return _mod.Portainer(
            "scavengarr", _PORTAINER, "key", sleep=lambda _: None, **kwargs
        )

    @respx.mock
    def test_run_returns_output_and_exit_code(self) -> None:
        listing = _docker_endpoint()
        create = respx.post(f"{_DOCKER}/containers/scavengarr/exec").respond(
            json={"Id": "e1"}
        )
        respx.post(f"{_DOCKER}/exec/e1/start").respond(content=_frame("hello\n"))
        respx.get(f"{_DOCKER}/exec/e1/json").respond(json={"ExitCode": 3})

        portainer = self._client()
        output, code = portainer.run(["python", "-c", "x"], env={"IDS": "a,b"})
        portainer.exec(["true"])

        assert (output, code) == ("hello\n", 3)
        body = json.loads(create.calls[0].request.content)
        assert body["Env"] == ["IDS=a,b"]
        assert create.calls[0].request.headers["X-API-Key"] == "key"
        # the label-applying listing runs once per client, not per request
        assert listing.call_count == 1

    @respx.mock
    def test_logs_are_demultiplexed(self) -> None:
        _docker_endpoint()
        logs = respx.get(f"{_DOCKER}/containers/scavengarr/logs").respond(
            content=_frame("line one\n") + _frame("line two\n", stream=2)
        )

        text = self._client().logs(1700000000, timestamps=True)

        assert text == "line one\nline two\n"
        assert logs.calls.last.request.url.params["since"] == "1700000000"
        assert logs.calls.last.request.url.params["timestamps"] == "1"

    @respx.mock
    def test_a_get_is_retried_on_an_overloaded_portainer(self) -> None:
        _docker_endpoint()
        stats = respx.get(f"{_DOCKER}/containers/scavengarr/stats")
        stats.side_effect = [
            httpx.Response(503),
            httpx.Response(200, json={"pids_stats": {"current": 7}}),
        ]

        assert self._client().stats() == {"pids_stats": {"current": 7}}
        assert stats.call_count == 2

    @respx.mock
    def test_an_exec_start_is_not_repeated_after_a_bad_gateway(self) -> None:
        _docker_endpoint()
        respx.post(f"{_DOCKER}/containers/scavengarr/exec").respond(json={"Id": "e1"})
        start = respx.post(f"{_DOCKER}/exec/e1/start").respond(status_code=502)

        with pytest.raises(httpx.HTTPStatusError):
            self._client().run(["python", "-c", "x"])
        assert start.call_count == 1

    @respx.mock
    def test_connection_errors_are_retried(self) -> None:
        endpoints = respx.get(f"{_PORTAINER}/api/endpoints")
        endpoints.side_effect = [
            httpx.ConnectError("refused"),
            httpx.Response(200, json=[{"Id": 3, "Type": 1}]),
        ]
        respx.get(f"{_DOCKER}/containers/json").respond(json=[{"Names": ["/a"]}])

        assert self._client().containers() == [{"Names": ["/a"]}]

    @respx.mock
    def test_every_request_passes_the_budget(self, tmp_path: Path) -> None:
        _docker_endpoint()
        respx.get(f"{_DOCKER}/containers/scavengarr/stats").respond(json={})
        budget = _mod.RequestBudget(tmp_path / "b.json")

        self._client(budget=budget).stats()

        assert len(json.loads((tmp_path / "b.json").read_text())) == 3

    @respx.mock
    def test_http_errors_raise(self) -> None:
        respx.get(f"{_PORTAINER}/api/endpoints").respond(status_code=401)

        with pytest.raises(httpx.HTTPStatusError):
            _mod.Portainer("scavengarr", _PORTAINER, "bad").logs(0)
