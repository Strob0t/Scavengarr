"""Tests for scripts/komodo.py (no live Komodo)."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_KOMODO = "http://komodo.test:9120"
_SECRET_ENV = "SCAVENGARR_CACHE_REDIS_PASSWORD=hunter2"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        return importlib.import_module("komodo")
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()


def _credentials(url: str = _KOMODO) -> Any:
    return _mod.Credentials(
        url=url,
        key="k3y",
        secret="s3cr3t",
        server="host-1",
        base_url="https://prod.test",
    )


# Docker's inspect object as Komodo answers it: the kept fields beside the
# container's environment, mounts and networks
_INSPECT: dict[str, Any] = {
    "Id": "c0ffee",
    "Name": "/scavengarr",
    "RestartCount": 2,
    "State": {
        "Status": "running",
        "Running": True,
        "OOMKilled": False,
        "StartedAt": "2026-10-06T11:09:00.123456789Z",
        "Health": {"Status": "healthy", "Log": [{"Output": _SECRET_ENV}]},
    },
    "HostConfig": {
        "Memory": 2 * 2**30,
        "NanoCpus": 2_000_000_000,
        "CpuQuota": 0,
        "CpuPeriod": 0,
        "Binds": ["/srv/private:/data"],
    },
    "Config": {"Env": [_SECRET_ENV, "PATH=/usr/bin"], "Image": "app:latest"},
    "Mounts": [{"Source": "/srv/private"}],
    "NetworkSettings": {"Networks": {"vpn": {"IPAddress": "172.18.0.5"}}},
}


def _log_answer(stdout: str, stderr: str = "", *, success: bool = True) -> Any:
    return {
        "stage": "get container log",
        "command": "docker logs --tail 5000 --timestamps -- scavengarr",
        "stdout": stdout,
        "stderr": stderr,
        "success": success,
        "start_ts": 1,
        "end_ts": 2,
    }


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

    def test_the_given_addresses_lose_their_host(self) -> None:
        """Komodo's and production's hosts stay out of an error message."""
        message = "Server error '503' for url 'https://Komodo.Example:9120/read'"
        masked = _mod.mask(message, "https://komodo.example:9120", "https://p.test")
        assert masked == "Server error '503' for url 'https://<host>:9120/…'"

    def test_a_short_host_leaves_words_alone(self) -> None:
        assert (
            _mod.mask("inbox http://box:9120/read", "http://box:9120")
            == "inbox http://<host>:9120/…"
        )


class TestCredentials:
    _FILE = (
        "OTHER=1\n"
        "KOMODO_URL=http://komodo.test:9120\n"
        "KOMODO_API_KEY='fresh'\n"
        'KOMODO_API_SECRET="s3cr3t"\n'
        "KOMODO_SERVER=host-1\n"
        "PRODCTL_BASE_URL=https://prod.test\n"
    )

    def test_the_env_file_wins_over_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env.devcontainer"
        env_file.write_text(self._FILE)
        monkeypatch.setenv("KOMODO_API_KEY", "stale")

        found = _mod.credentials(env_file)

        assert (found.url, found.key, found.secret, found.server, found.base_url) == (
            "http://komodo.test:9120",
            "fresh",
            "s3cr3t",
            "host-1",
            "https://prod.test",
        )

    def test_the_environment_fills_what_the_file_lacks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env.devcontainer"
        env_file.write_text(self._FILE.replace("KOMODO_SERVER=host-1\n", ""))
        monkeypatch.setenv("KOMODO_SERVER", "host-2")

        assert _mod.credentials(env_file).server == "host-2"

    def test_missing_names_stop_without_printing_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env.devcontainer"
        env_file.write_text("KOMODO_URL=http://komodo.test:9120\nKOMODO_API_KEY=k\n")
        for name in ("KOMODO_API_SECRET", "KOMODO_SERVER", "PRODCTL_BASE_URL"):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(SystemExit) as stop:
            _mod.credentials(env_file)

        message = str(stop.value)
        assert message.endswith(": KOMODO_API_SECRET, KOMODO_SERVER, PRODCTL_BASE_URL")
        assert "komodo.test" not in message

    def test_the_credentials_have_no_printable_form(self) -> None:
        shown = f"{_credentials()!r} {_credentials()}"
        for value in ("k3y", "s3cr3t", "komodo.test", "prod.test"):
            assert value not in shown


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


class TestReadRequestsOnly:
    def test_the_client_knows_one_path_and_two_request_types(self) -> None:
        """Komodo's key reaches more than reads: the client asks for nothing
        but ``/read`` with ``InspectContainer`` and ``GetContainerLog``."""
        tree = ast.parse((_SCRIPTS / "komodo.py").read_text())
        paths = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"/[a-z]+", node.value)
        }
        requests = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_read"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        assert paths == {"/read"}
        assert requests == {"InspectContainer", "GetContainerLog"}


class TestKomodo:
    def _client(self, sleeps: list[float] | None = None, **kwargs: Any) -> Any:
        sleep = sleeps.append if sleeps is not None else lambda _: None
        return _mod.Komodo(_credentials(), sleep=sleep, **kwargs)

    @respx.mock
    def test_inspect_is_one_read_request_with_key_and_secret(self) -> None:
        read = respx.post(f"{_KOMODO}/read").respond(json=_INSPECT)

        self._client().inspect()

        assert read.call_count == 1
        request = read.calls.last.request
        assert json.loads(request.content) == {
            "type": "InspectContainer",
            "params": {"server": "host-1", "container": "scavengarr"},
        }
        assert request.headers["X-Api-Key"] == "k3y"
        assert request.headers["X-Api-Secret"] == "s3cr3t"

    @respx.mock
    def test_inspect_keeps_the_state_and_the_limits_only(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        respx.post(f"{_KOMODO}/read").respond(json=_INSPECT)

        state = self._client().inspect()

        assert state == _mod.ContainerState(
            status="running",
            health="healthy",
            started_at="2026-10-06T11:09:00.123456789Z",
            oom_killed=False,
            restarts=2,
            memory=2 * 2**30,
            nano_cpus=2_000_000_000,
            cpu_quota=0,
            cpu_period=0,
        )
        assert state.cpus == 2.0
        assert {f.name for f in dataclasses.fields(state)} == {
            "status",
            "health",
            "started_at",
            "oom_killed",
            "restarts",
            "memory",
            "nano_cpus",
            "cpu_quota",
            "cpu_period",
        }
        shown = f"{state!r} {vars(state)}" + "".join(capsys.readouterr())
        for value in ("hunter2", "PASSWORD", "/srv/private", "172.18.0.5"):
            assert value not in shown

    @respx.mock
    def test_a_container_without_health_check_or_limits(self) -> None:
        respx.post(f"{_KOMODO}/read").respond(
            json={
                "State": {"Status": "exited", "OOMKilled": True, "Health": None},
                "HostConfig": {"Memory": 0, "NanoCpus": 0},
            }
        )

        state = self._client().inspect()

        assert (state.status, state.health, state.oom_killed) == ("exited", None, True)
        assert (state.restarts, state.memory, state.cpus) == (0, 0, None)

    def test_a_cpu_quota_is_a_limit_too(self) -> None:
        state = _mod.ContainerState(
            status="running",
            health=None,
            started_at="",
            oom_killed=False,
            restarts=0,
            memory=0,
            nano_cpus=0,
            cpu_quota=150_000,
            cpu_period=100_000,
        )
        assert state.cpus == 1.5

    @respx.mock
    def test_the_log_merges_stdout_and_stderr_by_their_times(self) -> None:
        """Komodo answers the two streams apart; ``docker logs`` interleaved
        them by time."""
        read = respx.post(f"{_KOMODO}/read").respond(
            json=_log_answer(
                "2026-10-06T11:09:01.000000001Z one\n"
                "2026-10-06T11:09:03.000000000Z three\n",
                "2026-10-06T11:09:02.500000000Z two\n"
                "2026-10-06T11:09:04.000000000Z four\n",
            )
        )

        log = self._client().log()

        assert [line.split(" ")[1] for line in log.lines] == [
            "one",
            "two",
            "three",
            "four",
        ]
        assert log.begins == "2026-10-06T11:09:01.000000001Z"
        assert log.text.endswith(" four\n")
        assert json.loads(read.calls.last.request.content) == {
            "type": "GetContainerLog",
            "params": {
                "server": "host-1",
                "container": "scavengarr",
                "tail": 5000,
                "timestamps": True,
            },
        }

    @respx.mock
    def test_an_empty_log_reaches_back_nowhere(self) -> None:
        respx.post(f"{_KOMODO}/read").respond(json=_log_answer(""))

        log = self._client().log()

        assert (log.lines, log.begins, log.text, log.cut) == ((), None, "", False)

    def test_a_log_of_the_cap_is_cut(self) -> None:
        line = "2026-10-06T11:09:01.000000000Z x"
        assert _mod.ContainerLog((line,) * 5000).cut
        assert not _mod.ContainerLog((line,) * 4999).cut

    @respx.mock
    def test_a_failed_log_command_raises_with_its_error(self) -> None:
        respx.post(f"{_KOMODO}/read").respond(
            json=_log_answer("", "Error: No such container: scavengarr", success=False)
        )

        with pytest.raises(_mod.KomodoError, match="No such container"):
            self._client().log()

    @respx.mock
    def test_a_read_is_retried_on_an_overloaded_komodo(self) -> None:
        read = respx.post(f"{_KOMODO}/read")
        read.side_effect = [
            httpx.Response(503, headers={"Retry-After": "7"}),
            httpx.Response(429, headers={"Retry-After": "600"}),
            httpx.Response(200, json=_INSPECT),
        ]
        sleeps: list[float] = []

        assert self._client(sleeps).inspect().status == "running"
        assert read.call_count == 3
        assert sleeps == [7.0, 60.0]

    @respx.mock
    def test_connection_errors_are_retried(self) -> None:
        read = respx.post(f"{_KOMODO}/read")
        read.side_effect = [httpx.ConnectError("refused"), httpx.Response(200, json={})]

        assert self._client().inspect().status == "unknown"
        assert read.call_count == 2

    @respx.mock
    def test_every_attempt_passes_the_budget(self, tmp_path: Path) -> None:
        read = respx.post(f"{_KOMODO}/read")
        read.side_effect = [httpx.Response(502), httpx.Response(200, json=_INSPECT)]
        budget = _mod.RequestBudget(tmp_path / "b.json")

        self._client(budget=budget).inspect()

        assert len(json.loads((tmp_path / "b.json").read_text())) == 2

    @respx.mock
    def test_a_refusal_names_komodos_reason_not_its_address(self) -> None:
        read = respx.post(f"{_KOMODO}/read").respond(
            403, json={"error": "invalid api key", "trace": []}
        )

        with pytest.raises(_mod.KomodoError) as error:
            self._client().inspect()

        assert str(error.value) == "InspectContainer answered 403: invalid api key"
        assert read.call_count == 1

    @respx.mock
    def test_the_last_attempt_raises(self) -> None:
        read = respx.post(f"{_KOMODO}/read").respond(503, text="<html>busy</html>")

        with pytest.raises(_mod.KomodoError, match="^GetContainerLog answered 503$"):
            self._client().log()
        assert read.call_count == 3

    @respx.mock
    def test_an_answer_that_is_no_json_object_raises(self) -> None:
        respx.post(f"{_KOMODO}/read").respond(200, text="<html>login</html>")

        with pytest.raises(_mod.KomodoError, match="answered no JSON object"):
            self._client().inspect()
