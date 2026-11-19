"""Tests for the post-deploy check's evaluation (``scripts/deploy_check.py``)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load("deploy_check")
_komodo = _load("komodo")

_START = 1791628020.0  # 2026-10-10T10:27:00Z


def _record(
    event: str, stamp: str = "2026-10-10T10:27:29Z", **fields: Any
) -> dict[str, Any]:
    return {"timestamp": stamp, "level": "info", "event": event, **fields}


def _startup_records() -> list[dict[str, Any]]:
    return [
        _record("config_unknown_keys", keys=["stremio.title_year_penalty"]),
        _record("hoster_state_restored", resolutions=32, redirects=36, breakers=6),
        _record("plugin_history_restored", plugins=17, days=68),
        _record("app_startup_complete", version="0.3.0", commit="unknown"),
    ]


def _stream(description: str) -> dict[str, Any]:
    return {"name": "Scavengarr\n1080p", "description": description, "url": "x"}


def _anime_answers(**overrides: list[dict[str, Any]]) -> list[Any]:
    """One answer per anime id with a matching stream, unless overridden
    (keyword ``series_tt0388629_5_2`` for ``series/tt0388629:5:2``)."""
    answers = []
    for n, (sid, code) in enumerate(_mod.ANIME_IDS):
        streams = overrides.get(sid.replace("/", "_").replace(":", "_")) or [
            _stream(f"Show.{code}.German.1080p\nGerman Dub\nVOE · site")
        ]
        answers.append(_mod.Answer(sid, 200, streams, 3.0 + n, request_id=f"r{n}"))
    return answers


class TestParseTime:
    def test_docker_nanoseconds(self) -> None:
        assert _mod.parse_time("2026-10-10T10:27:00.123456789Z") == pytest.approx(
            _START + 0.123456
        )

    def test_record_timestamps(self) -> None:
        assert _mod.parse_time("2026-10-10T10:27:00Z") == _START


def _state(**changes: Any) -> Any:
    fields: dict[str, Any] = {
        "status": "running",
        "health": "healthy",
        "started_at": "2026-10-10T10:27:00.123456789Z",
        "oom_killed": False,
        "restarts": 0,
        "memory": 0,
        "nano_cpus": 0,
        "cpu_quota": 0,
        "cpu_period": 0,
    }
    return _komodo.ContainerState(**{**fields, **changes})


class TestContainer:
    def test_running_and_healthy_passes(self) -> None:
        check = _mod.container_check(_state())

        assert (check.status, check.detail) == ("pass", "running, healthy, restarts 0")

    def test_restarts_and_an_oom_kill_show(self) -> None:
        check = _mod.container_check(_state(restarts=3, oom_killed=True))

        assert (check.status, check.detail) == (
            "pass",
            "running, healthy, restarts 3, OOM-killed",
        )

    @pytest.mark.parametrize(
        "changes",
        [
            {"health": "unhealthy"},
            {"health": "starting"},
            {"health": None},
            {"status": "exited", "health": None},
            {"status": "restarting"},
        ],
        ids=["unhealthy", "starting", "no-health-check", "exited", "restarting"],
    )
    def test_anything_else_fails(self, changes: dict[str, Any]) -> None:
        assert _mod.container_check(_state(**changes)).status == "fail"

    def test_no_health_check_is_named(self) -> None:
        detail = _mod.container_check(_state(health=None)).detail
        assert detail == "running, no health check, restarts 0"


class TestStartup:
    def test_the_starts_records_pass_with_the_unknown_keys_as_a_warning(self) -> None:
        checks = {c.name: c for c in _mod.startup_checks(_startup_records(), _START)}

        assert (checks["startup"].status, checks["startup"].detail) == (
            "pass",
            "version 0.3.0 at 10:27:29 UTC",
        )
        assert (checks["unknown keys"].status, checks["unknown keys"].detail) == (
            "warn",
            "stremio.title_year_penalty",
        )
        assert checks["hoster_state_restored"].detail == (
            "resolutions 32, redirects 36, breakers 6"
        )
        assert checks["plugin_history_restored"].detail == "plugins 17, days 68"
        assert not _mod.failed(checks.values())

    def test_a_startup_before_the_containers_start_fails(self) -> None:
        records = [
            _record("app_startup_complete", "2026-10-10T10:26:00Z", version="0.3.0")
        ]

        checks = {c.name: c for c in _mod.startup_checks(records, _START)}

        assert checks["startup"].status == "fail"
        assert "older" in checks["startup"].detail
        assert checks["unknown keys"].status == "pass"
        assert checks["hoster_state_restored"].status == "fail"

    def test_the_newest_startup_counts(self) -> None:
        records = [
            _record("app_startup_complete", "2026-10-10T09:00:00Z", version="0.2.9"),
            _record("app_startup_complete", version="0.3.0"),
        ]

        check = _mod.startup_checks(records, _START)[0]

        assert check.status == "pass"
        assert "0.3.0" in check.detail

    @pytest.mark.parametrize("version", [None, "unknown"])
    def test_without_a_version_the_startup_fails(self, version: str | None) -> None:
        fields = {"version": version} if version else {}
        records = [_record("app_startup_complete", **fields)]

        assert _mod.startup_checks(records, _START)[0].status == "fail"

    def test_a_log_cut_after_the_start_warns_instead_of_failing(self) -> None:
        """Komodo answers the log's last 5000 lines: after a long run they
        begin hours after the start, and its records are gone."""
        checks = _mod.startup_checks([], _START, cut_at=_START + 3600)

        assert [(c.name, c.status) for c in checks] == [
            ("startup", "warn"),
            ("unknown keys", "warn"),
            ("hoster_state_restored", "warn"),
            ("plugin_history_restored", "warn"),
        ]
        assert {c.detail for c in checks} == {
            "the log no longer reaches the container's start (it begins at"
            " 11:27:00 UTC)"
        }
        assert not _mod.failed(checks)

    def test_a_cut_log_that_still_reaches_the_start_is_judged(self) -> None:
        records = _startup_records()

        checks = _mod.startup_checks(records, _START, cut_at=_START - 60)

        assert checks == _mod.startup_checks(records, _START)


class TestStreams:
    def test_anime_ids_with_their_codes_and_the_movie_pass(self) -> None:
        answers = [
            *_anime_answers(),
            _mod.Answer(_mod.MOVIE_ID, 200, [_stream("x")], 20.0, "m"),
        ]
        records = [
            {"event": "http_request", "request_id": "r0", "duration_ms": 2900.0},
            {
                "event": "stremio_episode_ref",
                "request_id": "r0",
                "season": 5,
                "episode": 2,
                "absolute": 1089,
            },
            {"event": "http_request", "request_id": "m", "duration_ms": 19500.0},
        ]

        checks = _mod.stream_checks(answers, records, deadline_s=60)

        assert [c.status for c in checks] == ["pass"] * 4
        assert checks[0].detail == (
            "1 streams, all S05E02; logged; placed S05E02 abs 1089;"
            " client 3.0 s, server 2.9 s"
        )
        assert checks[1].detail == (
            "1 streams, all S22E04; not in the log; no placement record (DEBUG);"
            " client 4.0 s"
        )
        assert checks[3].detail == "1 streams; logged; client 20.0 s, server 19.5 s"

    def test_a_stream_of_another_episode_fails(self) -> None:
        answers = _anime_answers(
            series_tt0388629_5_2=[
                _stream("Show.S05E02.German\nVOE"),
                _stream("Show.S05E03.German\nVOE"),
            ]
        )

        check = _mod.stream_checks(answers, [], deadline_s=60)[0]

        assert check.status == "fail"
        assert check.detail.startswith("1 of 2 streams S05E02, others S05E03")

    def test_streams_without_the_code_fail_and_name_the_first(self) -> None:
        answers = _anime_answers(
            series_tt0388629_5_2=[_stream("One Piece\nGerman Dub")]
        )

        check = _mod.stream_checks(answers, [], deadline_s=60)[0]

        assert check.status == "fail"
        assert "none S05E02 (first: One Piece)" in check.detail

    def test_no_streams_fail(self) -> None:
        answers = [_mod.Answer(_mod.ANIME_IDS[0][0], 200, [], 1.0, "r")]

        check = _mod.stream_checks(answers, [], deadline_s=60)[0]

        assert (check.status, check.detail) == (
            "fail",
            "0 streams, none S05E02 (first: none); not in the log; client 1.0 s",
        )

    @pytest.mark.parametrize(
        ("answer", "detail"),
        [
            (
                ("series/tt0388629:5:2", 502, [], 0.4, None, None),
                "HTTP 502 after 0.4 s",
            ),
            (
                ("series/tt0388629:5:2", None, [], 120.0, None, "ReadTimeout"),
                "ReadTimeout after 120.0 s",
            ),
        ],
    )
    def test_an_error_answer_fails(self, answer: tuple[Any, ...], detail: str) -> None:
        sid, status, streams, seconds, rid, error = answer
        answers = [_mod.Answer(sid, status, streams, seconds, rid, error)]

        check = _mod.stream_checks(answers, [], deadline_s=60)[0]

        assert (check.status, check.detail) == ("fail", detail)

    def test_the_movie_fails_without_streams_or_over_the_deadline(self) -> None:
        answers = [
            _mod.Answer(_mod.MOVIE_ID, 200, [], 5.0, "a"),
            _mod.Answer(_mod.MOVIE_ID, 200, [_stream("x")], 61.0, "b"),
        ]

        checks = _mod.stream_checks(answers, [], deadline_s=60)

        assert [c.status for c in checks] == ["fail", "fail"]
        assert checks[0].detail == "no streams; client 5.0 s"
        assert checks[1].detail == (
            "1 streams but 61.0 s over the 60 s deadline; client 61.0 s"
        )


class TestClientTime:
    """Where an answer's time goes: the client's times to the headers and to
    the body's end beside the server's ``duration_ms``; more than 5 s on the
    way warns."""

    @staticmethod
    def _movie(seconds: float, headers: float | None = None) -> list[Any]:
        return [
            _mod.Answer(
                _mod.MOVIE_ID,
                200,
                [_stream("x")],
                seconds,
                "m",
                headers_seconds=headers,
            )
        ]

    @staticmethod
    def _served(duration_ms: float) -> list[dict[str, Any]]:
        return [
            {"event": "http_request", "request_id": "m", "duration_ms": duration_ms}
        ]

    def test_no_gap_passes_with_both_times(self) -> None:
        check = _mod.stream_checks(
            self._movie(4.2, headers=4.1), self._served(3900.0), deadline_s=60
        )[0]

        assert (check.status, check.detail) == (
            "pass",
            "1 streams; logged; client 4.1 s to headers, 4.2 s to the end,"
            " server 3.9 s",
        )

    def test_a_gap_warns_and_names_both_times(self) -> None:
        check = _mod.stream_checks(
            self._movie(27.4, headers=27.3), self._served(400.0), deadline_s=60
        )[0]

        assert (check.status, check.detail) == (
            "warn",
            "1 streams; logged; client 27.3 s to headers, 27.4 s to the end,"
            " server 0.4 s (27.0 s more on the way)",
        )

    def test_a_gap_of_5_s_still_passes(self) -> None:
        check = _mod.stream_checks(
            self._movie(6.0), self._served(1000.0), deadline_s=60
        )[0]

        assert check.status == "pass"

    def test_a_failed_check_stays_failed_with_a_gap(self) -> None:
        check = _mod.stream_checks(
            self._movie(70.0), self._served(400.0), deadline_s=60
        )[0]

        assert check.status == "fail"
        assert check.detail.endswith("server 0.4 s (69.6 s more on the way)")


class TestRender:
    def test_table_and_verdict(self) -> None:
        checks = [
            _mod.Check("container", "pass", "running, Up 2 hours (healthy)"),
            _mod.Check("unknown keys", "warn", "stremio.title_year_penalty"),
            _mod.Check("movie/tt0816692", "fail", "no streams, 5.0 s"),
        ]

        text = _mod.render(checks)

        assert "| container       | PASS   | running, Up 2 hours (healthy) |" in text
        assert "| unknown keys    | WARN   | stremio.title_year_penalty |" in text
        assert text.endswith("FAIL: 3 checks, 1 failed, 1 warnings")
        assert _mod.failed(checks)

    def test_a_clean_run_passes(self) -> None:
        checks = [_mod.Check("container", "pass", "ok")]

        assert _mod.render(checks).endswith("PASS: 1 checks, 0 failed, 0 warnings")
        assert not _mod.failed(checks)


class TestNetwork:
    """Check 7: the readiness probe's gauge (row 125)."""

    _METRICS = (
        "# HELP scavengarr_network_ok Whether the network probe's check answered\n"
        "# TYPE scavengarr_network_ok gauge\n"
        'scavengarr_network_ok{check="cache"} 1.0\n'
        'scavengarr_network_ok{check="dns"} 1.0\n'
    )

    def test_both_checks_answering_pass(self) -> None:
        check = _mod.network_check(self._METRICS)

        assert (check.name, check.status) == ("network", "pass")

    def test_a_check_that_failed_fails_and_is_named(self) -> None:
        check = _mod.network_check(self._METRICS.replace('"dns"} 1.0', '"dns"} 0.0'))

        assert check.status == "fail"
        assert "dns" in check.detail
        assert "cache" not in check.detail

    def test_without_a_probe_yet_it_warns(self) -> None:
        check = _mod.network_check('scavengarr_build_info{version="0.3.0"} 1.0\n')

        assert check.status == "warn"

    def test_metrics_that_did_not_answer_fail(self) -> None:
        assert _mod.network_check(None).status == "fail"
