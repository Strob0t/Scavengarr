"""Tests for scripts/stremio_profile.py (no live container)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_PORTAINER = "http://portainer.test:9000"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "stremio_profile", _SCRIPTS / "stremio_profile.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load()


def _frame(text: str, stream: int = 1) -> bytes:
    data = text.encode()
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def _log(event: str, **fields: Any) -> str:
    return json.dumps({"event": event, "level": "info", **fields})


class TestDemux:
    def test_joins_stdout_and_stderr_frames(self) -> None:
        raw = _frame("one\n") + _frame("two\n", stream=2)
        assert _mod.demux(raw) == "one\ntwo\n"

    def test_plain_text_passes_through(self) -> None:
        assert _mod.demux(b"plain log line\n") == "plain log line\n"


class TestCountRequests:
    def test_counts_httpx_requests_per_host(self) -> None:
        log = "\n".join(
            [
                _log('HTTP Request: GET https://voe.sx/e/a "HTTP/1.1 200 OK"'),
                _log('HTTP Request: GET https://voe.sx/e/b "HTTP/1.1 200 OK"'),
                _log('HTTP Request: HEAD https://kinoger.com/x "HTTP/1.1 302 Found"'),
                _log("stremio_search_start", title="Matrix"),
                "not json at all",
            ]
        )

        hosts = _mod.count_requests(log)

        assert hosts == {"voe.sx": 2, "kinoger.com": 1}


class TestCategorize:
    def test_shares_by_innermost_frame(self) -> None:
        profile = "\n".join(
            [
                "main (app.py:1);do_handshake (/usr/lib/python3.12/ssl.py:917) 30",
                "main (app.py:1);goahead (/usr/lib/python3.12/html/parser.py:1) 10",
                "main (app.py:1);_run_once (/usr/lib/python3.12/asyncio/base.py:1) 10",
                "main (app.py:1);parse_title (/app/plugins/sto.py:12) 50",
            ]
        )

        samples, shares = _mod.categorize(profile)

        assert samples == 100
        assert dict(shares) == {
            "other": 0.5,
            "TLS (ssl)": 0.3,
            "HTML parsing": 0.1,
            "event loop (asyncio/anyio/selectors)": 0.1,
        }

    def test_import_stacks_are_left_out(self) -> None:
        profile = (
            "_find_and_load (<frozen importlib._bootstrap>:1);"
            "exec (/usr/lib/python3.12/ssl.py:1) 40\n"
            "main (app.py:1);read (/usr/lib/python3.12/ssl.py:2) 10"
        )

        samples, shares = _mod.categorize(profile)

        assert samples == 10
        assert shares == [("TLS (ssl)", 1.0)]


class _FakeContainer:
    """CPU readings in order, one log for every request."""

    def __init__(self, cpu: list[dict[str, float]], log: str) -> None:
        self._cpu = list(cpu)
        self._log = log

    def exec(self, cmd: list[str], *, root: bool = False, detach: bool = False) -> str:
        return json.dumps(self._cpu.pop(0))

    def logs(self, since: int) -> str:
        return self._log


class TestMeasure:
    @respx.mock
    def test_one_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_mod.time, "sleep", lambda _s: None)
        respx.get(
            "https://scavengarr.test/api/v1/stremio/stream/movie/tt1.json"
        ).respond(json={"streams": [{"url": "a"}, {"url": "b"}]})
        container = _FakeContainer(
            [{"python": 10.0, "chrome": 5.0}, {"python": 14.5, "chrome": 7.0}],
            _log('HTTP Request: GET https://voe.sx/e/a "HTTP/1.1 200 OK"'),
        )

        with httpx.Client() as http:
            row = _mod.measure(http, "https://scavengarr.test", container, "movie/tt1")

        assert row["streams"] == 2
        assert row["python"] == pytest.approx(4.5)
        assert row["chrome"] == pytest.approx(2.0)
        assert row["requests"] == 1


class TestDockerCli:
    def test_exec_as_root_detached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def _run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", _run)

        _mod.DockerCli("scavengarr").exec(["py-spy", "record"], root=True, detach=True)

        assert calls == [
            ["docker", "exec", "--user", "root", "--detach", "scavengarr"]
            + ["py-spy", "record"]
        ]


class TestPortainer:
    @respx.mock
    def test_exec_runs_in_the_docker_environment(self) -> None:
        respx.get(f"{_PORTAINER}/api/endpoints").respond(
            json=[{"Id": 3, "Type": 1, "Name": "local"}]
        )
        listing = respx.get(f"{_PORTAINER}/api/endpoints/3/docker/containers/json")
        listing.respond(json=[])
        create = respx.post(
            f"{_PORTAINER}/api/endpoints/3/docker/containers/scavengarr/exec"
        ).respond(json={"Id": "e1"})
        respx.post(f"{_PORTAINER}/api/endpoints/3/docker/exec/e1/start").respond(
            content=_frame('{"python": 1.5, "chrome": 2.0}\n')
        )
        portainer = _mod.Portainer("scavengarr", _PORTAINER, "key")

        cpu = _mod.cpu_seconds(portainer)

        assert cpu == {"python": 1.5, "chrome": 2.0}
        body = json.loads(create.calls.last.request.content)
        assert body["Cmd"][:2] == ["python", "-c"]
        assert "User" not in body
        # listing first: Portainer applies access labels of recreated containers
        assert listing.called
        assert create.calls.last.request.headers["X-API-Key"] == "key"

    @respx.mock
    def test_logs_are_demultiplexed(self) -> None:
        respx.get(f"{_PORTAINER}/api/endpoints").respond(json=[{"Id": 3, "Type": 1}])
        respx.get(f"{_PORTAINER}/api/endpoints/3/docker/containers/json").respond(
            json=[]
        )
        logs = respx.get(
            f"{_PORTAINER}/api/endpoints/3/docker/containers/scavengarr/logs"
        ).respond(content=_frame("line one\n") + _frame("line two\n", stream=2))

        text = _mod.Portainer("scavengarr", _PORTAINER, "key").logs(1700000000)

        assert text == "line one\nline two\n"
        assert logs.calls.last.request.url.params["since"] == "1700000000"

    @respx.mock
    def test_http_errors_raise(self) -> None:
        respx.get(f"{_PORTAINER}/api/endpoints").respond(status_code=401)

        with pytest.raises(httpx.HTTPStatusError):
            _mod.Portainer("scavengarr", _PORTAINER, "bad").logs(0)
