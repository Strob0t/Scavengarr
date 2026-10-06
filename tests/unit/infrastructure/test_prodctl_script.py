"""Tests for scripts/prodctl.py and scripts/probes/ (no live Portainer)."""

from __future__ import annotations

import argparse
import importlib
import json
import py_compile
import sys
from pathlib import Path
from types import ModuleType

import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_PORTAINER = "http://portainer.test:9000"
_DOCKER = f"{_PORTAINER}/api/endpoints/3/docker"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))  # the script imports portainer
    try:
        return importlib.import_module("prodctl")
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()


def _frame(text: str) -> bytes:
    data = text.encode()
    return bytes([1, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


def _record(**fields: object) -> str:
    return "2026-10-06T11:09:08.998479784Z " + json.dumps(
        {"timestamp": "2026-10-06T11:09:08Z", **fields}
    )


@pytest.fixture
def portainer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fake credentials and a private request budget; the Docker endpoint."""
    monkeypatch.setattr(_mod, "credentials", lambda: (_PORTAINER, "key"))
    monkeypatch.setattr(_mod, "_BUDGET", tmp_path / "budget.json")
    respx.get(f"{_PORTAINER}/api/endpoints").respond(json=[{"Id": 3, "Type": 1}])
    respx.get(f"{_DOCKER}/containers/json").respond(json=[])
    return tmp_path


class TestSeconds:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("90s", 90), ("15m", 900), ("2h", 7200), ("1d", 86400), ("30", 30)],
    )
    def test_durations(self, text: str, expected: float) -> None:
        assert _mod.seconds(text) == expected

    def test_garbage_is_rejected(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            _mod.seconds("soon")


class TestRender:
    def test_json_records_become_key_value_pairs(self) -> None:
        line = _record(level="info", event="http_request", path="/x", status_code=200)
        assert _mod.render(line) == "11:09:08 info http_request path=/x status_code=200"

    def test_fields_select_and_order_the_keys(self) -> None:
        line = _record(level="info", event="e", a=1, b=2, c=3)
        assert _mod.render(line, ["c", "a", "missing"]) == "11:09:08 info e c=3 a=1"

    def test_output_is_masked(self) -> None:
        line = _record(
            level="warning",
            event="HTTP Request: GET https://cdn.example.net/t0k3n/v.mp4?i=10.1.2.3",
            client_host="172.18.0.1",
        )
        rendered = _mod.render(line)
        assert "t0k3n" not in rendered
        assert "10.1.2.3" not in rendered
        assert rendered.endswith("https://cdn.example.net/… client_host=<ip>")

    def test_plain_lines_pass_through_masked(self) -> None:
        line = "2026-10-06T11:09:08.9Z INFO: 172.18.0.1:42662 - GET /api/v1/x"
        assert _mod.render(line) == "11:09:08 INFO: <ip>:42662 - GET /api/v1/x"


class TestSelectLines:
    _TEXT = "\n".join(
        [
            _record(event="http_request", path="/api/v1/healthz"),
            _record(event="stremio_search_start", plugin="kinoger"),
            _record(event="plugin_search_done", plugin="Kinoger"),
            _record(event="plugin_search_done", plugin="moflix"),
        ]
    )

    def test_health_checks_are_dropped_unless_asked_for(self) -> None:
        assert _mod.select_lines(self._TEXT, grep=None, health=False, limit=0)[1] == 3
        assert _mod.select_lines(self._TEXT, grep=None, health=True, limit=0)[1] == 4

    def test_grep_ignores_case_and_limit_keeps_the_last_lines(self) -> None:
        lines, matched = _mod.select_lines(
            self._TEXT, grep="kinoger", health=False, limit=1
        )
        assert matched == 2
        assert lines == [self._TEXT.splitlines()[2]]


class TestStatsLine:
    def test_cores_memory_processes_and_network(self) -> None:
        sample = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 3_000},
                "system_cpu_usage": 10_000,
                "online_cpus": 4,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 1_000},
                "system_cpu_usage": 6_000,
            },
            "memory_stats": {
                "usage": 300 * 2**20,
                "limit": 2**30,
                "stats": {"inactive_file": 200 * 2**20},
            },
            "pids_stats": {"current": 7},
            "networks": {"eth0": {"rx_bytes": 2048, "tx_bytes": 512}},
        }
        assert _mod.stats_line("app", sample) == (
            "app: CPU 2.00 of 4 cores | memory 100.0 MiB of 1.0 GiB | processes 7"
            " | network rx 2.0 KiB tx 512 B"
        )


class TestProbes:
    def test_a_probe_is_found_by_name_or_path(self, tmp_path: Path) -> None:
        own = tmp_path / "mine.py"
        own.write_text("print(1)\n")
        assert "asyncio" in _mod.probe_source("tasks")
        assert _mod.probe_source(str(own)) == "print(1)\n"

    def test_an_unknown_probe_names_the_known_ones(self) -> None:
        with pytest.raises(SystemExit, match="known: .*resources"):
            _mod.probe_source("nope")

    @pytest.mark.parametrize(
        "path", sorted((_SCRIPTS / "probes").glob("*.py")), ids=lambda p: p.stem
    )
    def test_every_probe_compiles_and_says_what_it_reads(self, path: Path) -> None:
        py_compile.compile(str(path), doraise=True)
        source = path.read_text()
        assert source.startswith('"""'), "probes start with a docstring"
        assert "from __future__ import annotations" in source


class TestCommands:
    @respx.mock
    def test_logs_print_masked_lines_and_a_count(
        self, portainer: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        log = "\n".join(
            [
                _record(level="info", event="http_request", path="/api/v1/healthz"),
                _record(level="info", event="resolved", url="https://cdn.x.io/a?t=1"),
            ]
        )
        logs = respx.get(f"{_DOCKER}/containers/scavengarr/logs").respond(
            content=_frame(log + "\n")
        )

        assert _mod.main(["logs", "--since", "5m"]) == 0

        out = capsys.readouterr().out.splitlines()
        assert out == [
            "11:09:08 info resolved url=https://cdn.x.io/…",
            "[1 of 1 matching lines]",
        ]
        assert logs.calls.last.request.url.params["timestamps"] == "1"
        assert (portainer / "budget.json").exists()

    @respx.mock
    def test_a_probe_runs_in_the_container_and_passes_its_exit_code(
        self, portainer: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        create = respx.post(f"{_DOCKER}/containers/stremio/exec").respond(
            json={"Id": "e1"}
        )
        respx.post(f"{_DOCKER}/exec/e1/start").respond(
            content=_frame("from 10.9.8.7\n")
        )
        respx.get(f"{_DOCKER}/exec/e1/json").respond(json={"ExitCode": 2})

        code = _mod.main(["probe", "-c", "stremio", "--env", "A=1", "tasks", "ps"])

        assert code == 2
        body = json.loads(create.calls.last.request.content)
        assert body["Cmd"][0:2] == ["python", "-c"]
        assert body["Cmd"][3:] == ["ps"]
        assert body["Env"] == ["A=1"]
        out = capsys.readouterr().out
        assert "from <ip>" in out
        assert "[exit 2 after" in out

    def test_env_pairs_need_an_equals_sign(self) -> None:
        with pytest.raises(SystemExit, match="KEY=VALUE"):
            _mod.main(["probe", "--env", "oops", "tasks"])
