"""Tests for scripts/prodctl.py and scripts/probes/ (no live Komodo or app)."""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import py_compile
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_KOMODO = "http://komodo.test:9120"
_PUBLIC = "https://addon.test"
_STAMP = "2026-10-06T11:09:08.998479784Z"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))  # the script imports komodo and digest
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load("prodctl")
_komodo = _load("komodo")
# prodctl's clock in the commands' tests: a minute after the records
_NOW = _mod.parse_time("2026-10-06T11:10:08Z")


def _record(stamp: str = _STAMP, **fields: object) -> str:
    return f"{stamp} " + json.dumps({"timestamp": stamp[:19] + "Z", **fields})


# Docker's inspect object as Komodo answers it, the environment beside the
# kept fields
_INSPECT: dict[str, Any] = {
    "RestartCount": 0,
    "State": {
        "Status": "running",
        "OOMKilled": False,
        "StartedAt": "2026-10-06T11:09:00.123456789Z",
        "Health": {"Status": "healthy"},
    },
    "HostConfig": {"Memory": 2 * 2**30, "NanoCpus": 4_000_000_000},
    "Config": {"Env": ["SCAVENGARR_CACHE_REDIS_PASSWORD=hunter2"]},
}


def _state(**changes: Any) -> Any:
    fields: dict[str, Any] = {
        "status": "running",
        "health": "healthy",
        "started_at": "2026-10-06T11:09:00.123456789Z",
        "oom_killed": False,
        "restarts": 0,
        "memory": 2 * 2**30,
        "nano_cpus": 4_000_000_000,
        "cpu_quota": 0,
        "cpu_period": 0,
    }
    return _komodo.ContainerState(**{**fields, **changes})


@pytest.fixture
def production(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fake credentials, a private request budget and a fixed clock.

    The mock starts here, before the routes: a route added before
    ``respx.mock`` starts is part of its snapshot and outlives the test.
    """
    found = _komodo.Credentials(
        url=_KOMODO, key="k3y", secret="s3cr3t", server="host-1", base_url=_PUBLIC
    )
    monkeypatch.setattr(_mod, "credentials", lambda: found)
    monkeypatch.setattr(_mod, "_BUDGET", tmp_path / "budget.json")
    monkeypatch.setattr(
        _mod,
        "time",
        SimpleNamespace(time=lambda: _NOW, monotonic=time.monotonic, sleep=lambda _: 0),
    )
    with respx.mock:
        yield tmp_path


def _komodo_reads(
    *, inspect: dict[str, Any] | None = None, stdout: str = "", stderr: str = ""
) -> respx.Route:
    """Komodo's ``/read``: the inspect object or the log, by the type."""

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["type"] == "InspectContainer":
            return httpx.Response(200, json=_INSPECT if inspect is None else inspect)
        assert body["type"] == "GetContainerLog"
        log = {"stdout": stdout, "stderr": stderr, "success": True}
        return httpx.Response(200, json=log)

    return respx.post(f"{_KOMODO}/read").mock(side_effect=answer)


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


class TestWindow:
    _LOG = _komodo.ContainerLog(
        (
            "2026-10-06T11:00:00.000000000Z a",
            "2026-10-06T11:05:00.000000000Z b",
            "2026-10-06T11:10:00.000000000Z c",
        )
    )

    def test_since_keeps_the_lines_from_the_start_on(self) -> None:
        start = _mod.parse_time("2026-10-06T11:04:00Z")
        assert _mod.since(self._LOG, start) == list(self._LOG.lines[1:])
        assert _mod.since(self._LOG, _NOW) == []

    def test_a_log_beginning_after_the_start_names_its_first_line(self) -> None:
        early = _mod.parse_time("2026-10-06T10:00:00Z")
        late = _mod.parse_time("2026-10-06T11:30:00Z")
        assert _mod.reach(self._LOG, early) == _mod.parse_time("2026-10-06T11:00:00Z")
        assert _mod.reach(self._LOG, late) is None
        assert _mod.reach(_komodo.ContainerLog(()), early) is None


class TestStatsLine:
    _FIRST = {"process_cpu_seconds_total": 100.0}
    _SECOND = {
        "process_cpu_seconds_total": 110.0,
        "process_resident_memory_bytes": 300 * 2**20,
        "process_open_fds": 87.0,
    }

    def test_cores_memory_and_files_against_the_limits(self) -> None:
        assert _mod.stats_line(_state(), self._FIRST, self._SECOND, 5.0) == (
            "scavengarr app process: CPU 2.000 of 4 cores | memory 300.0 MiB of"
            " 2.0 GiB | open files 87"
        )

    def test_an_idle_app_shows_its_load(self) -> None:
        """``stats`` once printed "CPU 0.00" for an idle app (0.0035 cores,
        rounded to two decimals), which read as a missing sample."""
        second = {**self._SECOND, "process_cpu_seconds_total": 100.02}
        line = _mod.stats_line(_state(), self._FIRST, second, 5.0)
        assert line.startswith("scavengarr app process: CPU 0.004 of 4 cores | ")

    def test_without_limits_or_samples(self) -> None:
        line = _mod.stats_line(_state(memory=0, nano_cpus=0), {}, {}, 5.0)
        assert line == (
            "scavengarr app process: CPU ? cores (no limit) | memory ? (no limit)"
            " | open files ?"
        )

    def test_the_process_counters_of_one_read(self) -> None:
        text = (
            "# HELP process_cpu_seconds_total Total user and system CPU time\n"
            "process_cpu_seconds_total 12.5\n"
            "process_resident_memory_bytes 3.145728e+08\n"
            "process_open_fds 87.0\n"
            'scavengarr_hls_proxy_total{kind="segment"} 3.0\n'
        )
        assert _mod.process_counters(text) == {
            "process_cpu_seconds_total": 12.5,
            "process_resident_memory_bytes": 314572800.0,
            "process_open_fds": 87.0,
        }


# The probes that run inside the production container, by the maintainer's
# docker exec on the Docker host (the table in docs/how-to/operations.md)
_CONTAINER_PROBES = (
    "anime_ids",
    "capture_state",
    "capture_timing",
    "domains",
    "hls_throughput",
    "links",
    "redis_cache",
    "resources",
    "sto_linkout",
    "stream_quality",
    "tasks",
    "vidhide_segments",
)


def _docstring(path: Path) -> str:
    return ast.get_docstring(ast.parse(path.read_text())) or ""


class TestProbeFiles:
    @pytest.mark.parametrize(
        "path", sorted((_SCRIPTS / "probes").glob("*.py")), ids=lambda p: p.stem
    )
    def test_every_probe_compiles_and_says_what_it_reads(self, path: Path) -> None:
        py_compile.compile(str(path), doraise=True)
        source = path.read_text()
        assert source.startswith('"""'), "probes start with a docstring"
        assert "from __future__ import annotations" in source

    @pytest.mark.parametrize("name", _CONTAINER_PROBES)
    def test_a_container_probe_names_its_docker_exec(self, name: str) -> None:
        doc = _docstring(_SCRIPTS / "probes" / f"{name}.py")
        assert "docker exec -i scavengarr python -" in doc
        assert f"< scripts/probes/{name}.py" in doc

    @pytest.mark.parametrize(
        "path", sorted((_SCRIPTS / "probes").glob("*.py")), ids=lambda p: p.stem
    )
    def test_no_probe_names_the_removed_probe_command(self, path: Path) -> None:
        doc = _docstring(path)
        names = "|".join(p.stem for p in (_SCRIPTS / "probes").glob("*.py"))
        assert "prodctl.py probe" not in doc
        assert not re.search(rf"(``|^\s*)probe ({names})\b", doc, re.MULTILINE)


class TestParser:
    def test_insecure_is_a_top_level_flag(self) -> None:
        assert _mod.parser().parse_args(["--insecure", "check"]).insecure
        assert not _mod.parser().parse_args(["ps"]).insecure

    @pytest.mark.parametrize(
        "argv",
        [["check", "--insecure"], ["logs", "-c", "other"], ["probe", "tasks"]],
        ids=["insecure-after-the-command", "container", "probe"],
    )
    def test_the_removed_options_are_refused(self, argv: list[str]) -> None:
        with pytest.raises(SystemExit):
            _mod.parser().parse_args(argv)


class TestErrors:
    """An error ends with the masked message, not a traceback, and names
    neither Komodo's host nor production's."""

    def test_a_komodo_refusal_prints_its_reason(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        respx.post(f"{_KOMODO}/read").respond(403, json={"error": "invalid api key"})

        assert _mod.main(["logs", "--since", "5m"]) == 1

        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            captured.err
            == "logs failed: GetContainerLog answered 403: invalid api key\n"
        )

    def test_a_transport_error_names_no_host(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        respx.post(f"{_KOMODO}/read").mock(
            side_effect=httpx.ReadTimeout("timed out reading http://komodo.test:9120")
        )

        assert _mod.main(["ps"]) == 1

        err = capsys.readouterr().err
        assert err == "ps failed: timed out reading http://<host>:9120\n"

    def test_a_public_endpoint_error_names_no_host(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        respx.get(f"{_PUBLIC}/metrics").respond(404)

        assert _mod.main(["metrics"]) == 1

        err = capsys.readouterr().err
        assert "404 Not Found" in err
        assert "https://<host>/…" in err
        assert "addon.test" not in err


class TestCommands:
    def test_ps_prints_the_containers_state_without_its_environment(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reads = _komodo_reads()

        assert _mod.main(["ps"]) == 0

        out = capsys.readouterr().out
        assert out == (
            "scavengarr | running (healthy) | started 2026-10-06 11:09:00 UTC"
            " | restarts 0 | OOM-killed no\n"
        )
        assert "hunter2" not in out
        assert reads.call_count == 1
        assert (production / "budget.json").exists()

    def test_stats_reads_two_metrics_samples_5_s_apart(
        self,
        production: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _komodo_reads()
        samples = iter(
            [
                "process_cpu_seconds_total 100.0\n",
                "process_cpu_seconds_total 110.0\n"
                "process_resident_memory_bytes 3.145728e+08\n"
                "process_open_fds 87.0\n",
            ]
        )
        respx.get(f"{_PUBLIC}/metrics").mock(
            side_effect=lambda _: httpx.Response(200, text=next(samples))
        )
        clock = iter([10.0, 15.0])
        sleeps: list[float] = []
        monkeypatch.setattr(
            _mod,
            "time",
            SimpleNamespace(monotonic=lambda: next(clock), sleep=sleeps.append),
        )

        assert _mod.main(["stats"]) == 0

        assert capsys.readouterr().out.splitlines() == [
            "scavengarr app process: CPU 2.000 of 4 cores | memory 300.0 MiB of"
            " 2.0 GiB | open files 87",
            "[the app's Python process: Chromium's processes are not in it]",
        ]
        assert sleeps == [5.0]

    def test_logs_print_masked_lines_and_a_count(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        old = _record("2026-10-06T10:00:00.000000000Z", level="info", event="old", n=1)
        stdout = "\n".join(
            [
                old,
                _record(level="info", event="http_request", path="/api/v1/healthz"),
                _record(level="info", event="resolved", url="https://cdn.x.io/a?t=1"),
            ]
        )
        _komodo_reads(stdout=stdout + "\n", stderr=_record(level="error", event="e"))

        assert _mod.main(["logs", "--since", "5m"]) == 0

        assert capsys.readouterr().out.splitlines() == [
            "11:09:08 info resolved url=https://cdn.x.io/…",
            "11:09:08 error e",
            "[2 of 2 matching lines]",
        ]

    def test_logs_name_how_far_back_a_cut_log_reaches(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Komodo answers the log's last 5000 lines: about 4 h of it."""
        lines = [
            f"2026-10-06T{8 + n // 3600:02d}:{n // 60 % 60:02d}:{n % 60:02d}"
            f".000000000Z line {n}"
            for n in range(5000)
        ]
        _komodo_reads(stdout="\n".join(lines))

        assert _mod.main(["logs", "--since", "6h", "--limit", "1"]) == 0

        assert capsys.readouterr().out.splitlines() == [
            "09:23:19 line 4999",
            "[1 of 5000 matching lines]",
            "[the log reaches back to 2026-10-06 08:00:00 UTC only: Komodo answers"
            " its last 5000 lines]",
        ]

    def test_logs_cut_records_unless_asked_for_the_whole(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A traceback's last lines name the error."""
        log = _record(level="error", event="crash", exception="x" * 500 + "KeyError")
        _komodo_reads(stdout=log + "\n")

        _mod.main(["logs", "--since", "5m"])
        cut = capsys.readouterr().out.splitlines()[0]
        _mod.main(["logs", "--since", "5m", "--width", "0"])
        whole = capsys.readouterr().out.splitlines()[0]

        assert len(cut) == 400
        assert whole.endswith("KeyError")

    def test_metrics_and_state_come_from_the_public_url(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        respx.get(f"{_PUBLIC}/metrics").respond(
            text=(
                "# HELP scavengarr_up up\n"
                "scavengarr_up 1.0\n"
                'scavengarr_client{host="10.1.2.3"} 2.0\n'
                "process_open_fds 9.0\n"
            )
        )
        respx.get(f"{_PUBLIC}/api/v1/stats/metrics").respond(
            json={"breakers": {"voe": "open"}, "loop_lag": 0.01}
        )

        assert _mod.main(["metrics", "--grep", "scavengarr"]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "scavengarr_up 1.0",
            'scavengarr_client{host="<ip>"} 2.0',
        ]
        assert _mod.main(["state", "--keys", "breakers"]) == 0
        assert json.loads(capsys.readouterr().out) == {"breakers": {"voe": "open"}}

    def test_the_digest_reads_the_log_and_two_endpoints(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reads = _komodo_reads(
            stdout=_record(
                level="error", event="crash", exception="x\nhttps://cdn.x.io/a?t=1"
            )
            + "\n"
        )
        metrics = respx.get(f"{_PUBLIC}/metrics").respond(
            text='scavengarr_build_info{commit="abc",version="0.3.0"} 1.0\n'
        )
        respx.get(f"{_PUBLIC}/api/v1/stats/plugins").respond(404)

        assert _mod.main(["digest", "--since", "2h"]) == 0

        out = capsys.readouterr().out
        # The log's one line is a minute old
        assert out.startswith(
            "# scavengarr: the last 2.0 h (the log reaches back 1 min only)\n"
        )
        assert "version 0.3.0, commit abc" in out
        assert "The long-term record is not available" in out
        assert "| crash: https://cdn.x.io/… | 1 |" in out
        assert reads.call_count == 1 and metrics.call_count == 1

        assert _mod.main(["digest", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["since_s"] == 86400
        assert data["log_s"] == pytest.approx(59, abs=1)
        assert data["errors"]["count"] == 1


class TestStreamAnswers:
    def test_the_times_to_the_headers_and_to_the_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The body's end comes after the headers: a slow body shows."""
        clock = iter([10.0, 12.5, 14.0])
        monkeypatch.setattr(
            _mod, "time", SimpleNamespace(monotonic=lambda: next(clock))
        )
        with respx.mock:
            respx.get("https://addon.test/stream/movie/tt1.json").respond(
                json={"streams": [{"url": "x"}]}, headers={"X-Request-ID": "r1"}
            )

            [answer] = _mod.stream_answers(
                "https://addon.test", ["movie/tt1"], insecure=False, timeout=5
            )

        assert (answer.headers_seconds, answer.seconds) == (2.5, 4.0)
        assert (answer.status, answer.request_id, len(answer.streams)) == (200, "r1", 1)

    def test_an_error_keeps_its_time_without_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = iter([10.0, 15.0])
        monkeypatch.setattr(
            _mod, "time", SimpleNamespace(monotonic=lambda: next(clock))
        )
        with respx.mock:
            respx.get("https://addon.test/stream/movie/tt1.json").mock(
                side_effect=httpx.ReadTimeout("slow")
            )

            [answer] = _mod.stream_answers(
                "https://addon.test", ["movie/tt1"], insecure=False, timeout=5
            )

        assert (answer.error, answer.seconds, answer.headers_seconds) == (
            "ReadTimeout",
            5.0,
            None,
        )


class TestCheck:
    _ADDON = f"{_PUBLIC}/api/v1/stremio"
    _CODES = {
        "series/tt0388629:5:2": "S05E02",
        "series/kitsu:12:1089": "S22E04",
        "series/tt9335498:2:1": "S02E01",
        "movie/tt0816692": "Interstellar.2014",
    }
    _RECORDS = (
        _record(
            level="warning",
            event="config_unknown_keys",
            keys=["stremio.title_year_penalty"],
        ),
        _record(
            level="info",
            event="hoster_state_restored",
            resolutions=32,
            redirects=36,
            breakers=6,
        ),
        _record(level="info", event="plugin_history_restored", plugins=17, days=68),
        _record(level="info", event="app_startup_complete", version="0.3.0"),
        _record(
            level="info",
            event="http_request",
            request_id="r1",
            path="/api/v1/stremio/stream/series/tt0388629:5:2.json",
            duration_ms=412.5,
            client_host="10.0.0.9",
        ),
    )

    @pytest.fixture(autouse=True)
    def _fixed_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """prodctl's clock stands still, as in TestStreamAnswers: the rows
        read 0.0 s however slow the runner, and the settle wait returns at
        once."""
        monkeypatch.setattr(
            _mod,
            "time",
            SimpleNamespace(
                monotonic=lambda: 0.0, sleep=lambda _: None, time=time.time
            ),
        )

    @staticmethod
    def _stream(description: str) -> dict[str, str]:
        return {"name": "Scavengarr\n1080p", "description": description, "url": "x"}

    def _mock_production(
        self, *, healthy: bool = True, dns: float = 1.0, log: list[str] | None = None
    ) -> None:
        inspect = json.loads(json.dumps(_INSPECT))
        inspect["State"]["Health"]["Status"] = "healthy" if healthy else "unhealthy"
        _komodo_reads(inspect=inspect, stdout="\n".join(log or self._RECORDS) + "\n")
        # the readiness probe's gauge per check
        respx.get(f"{_PUBLIC}/metrics").respond(
            text=(
                f'scavengarr_network_ok{{check="cache"}} 1.0\n'
                f'scavengarr_network_ok{{check="dns"}} {dns}\n'
            )
        )

    def _mock_streams(self, codes: dict[str, str]) -> None:
        for sid, code in codes.items():
            respx.get(f"{self._ADDON}/stream/{sid}.json").respond(
                json={"streams": [self._stream(f"Show.{code}\nVOE · site")]},
                headers={"X-Request-ID": "r1"},
            )

    def test_a_clean_deploy_passes(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._mock_production()
        self._mock_streams(self._CODES)

        # --base defaults to PRODCTL_BASE_URL's addon
        code = _mod.main(["--insecure", "check"])

        out = capsys.readouterr().out
        assert code == 0, out
        assert out.endswith("PASS: 10 checks, 0 failed, 1 warnings\n")
        rows = [
            "| container               | PASS   | running, healthy, restarts 0 |",
            "| startup                 | PASS   | version 0.3.0 at 11:09:08 UTC |",
            "| unknown keys            | WARN   | stremio.title_year_penalty |",
            "| hoster_state_restored   | PASS   | resolutions 32, redirects 36,"
            " breakers 6 |",
            "| plugin_history_restored | PASS   | plugins 17, days 68 |",
            "| series/tt0388629:5:2    | PASS   | 1 streams, all S05E02; logged;"
            " no placement record (DEBUG); client 0.0 s to headers, 0.0 s to the"
            " end, server 0.4 s |",
            "| movie/tt0816692         | PASS   | 1 streams; logged; client 0.0 s"
            " to headers, 0.0 s to the end, server 0.4 s |",
            "| network                 | PASS   | cache and dns answered |",
        ]
        for row in rows:
            assert row in out, row
        assert "10.0.0.9" not in out

    def test_a_wrong_episode_or_an_unhealthy_container_fails(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._mock_production(healthy=False, dns=0.0)
        self._mock_streams({**self._CODES, "series/tt0388629:5:2": "S05E03"})

        code = _mod.main(["check", "--base", self._ADDON])

        out = capsys.readouterr().out
        assert code == 1
        assert (
            "| container               | FAIL   | running, unhealthy, restarts 0 |"
            in out
        )
        assert (
            "| series/tt0388629:5:2    | FAIL   | 1 streams, none S05E02"
            " (first: Show.S05E03); logged; client 0.0 s to headers, 0.0 s to the"
            " end, server 0.4 s |"
        ) in out
        assert (
            "| network                 | FAIL   | no answer at the last probe: dns |"
            in out
        )
        assert out.endswith("FAIL: 10 checks, 3 failed, 1 warnings\n")

    def test_a_log_cut_after_the_start_warns_on_the_starts_records(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """5000 lines that begin after the container's start: its startup
        records are gone, which is no failure of the deploy."""
        filler = [f"2026-10-06T11:09:05.000000000Z filler {n}" for n in range(4999)]
        self._mock_production(log=[*filler, self._RECORDS[-1]])
        self._mock_streams(self._CODES)

        code = _mod.main(["check"])

        out = capsys.readouterr().out
        assert code == 0, out
        detail = (
            "the log no longer reaches the container's start"
            " (it begins at 11:09:05 UTC)"
        )
        for name in (
            "startup                ",
            "unknown keys           ",
            "hoster_state_restored  ",
            "plugin_history_restored",
        ):
            assert f"| {name} | WARN   | {detail} |" in out
        assert out.endswith("PASS: 10 checks, 0 failed, 4 warnings\n")

    def test_the_records_before_the_start_do_not_count(
        self, production: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A restart keeps the container's log: the restore records of the
        run before it pass nothing."""
        before = "2026-10-06T10:00:00.000000000Z"
        log = [
            _record(before, level="info", event="hoster_state_restored", breakers=1),
            _record(before, level="info", event="plugin_history_restored", days=1),
            *self._RECORDS[3:],
        ]
        self._mock_production(log=log)
        self._mock_streams(self._CODES)

        assert _mod.main(["check"]) == 1

        out = capsys.readouterr().out
        assert "| hoster_state_restored   | FAIL   | no record since the start |" in out
        assert "| startup                 | PASS   |" in out
