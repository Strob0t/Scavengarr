"""Tests for the post-deploy check's evaluation (``scripts/deploy_check.py``)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        return importlib.import_module("deploy_check")
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()

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


class TestContainer:
    def test_running_and_healthy_passes(self) -> None:
        rows = [
            {
                "Names": ["/scavengarr"],
                "State": "running",
                "Status": "Up 2 hours (healthy)",
            }
        ]

        check = _mod.container_check(rows, "scavengarr")

        assert (check.status, check.detail) == ("pass", "running, Up 2 hours (healthy)")

    @pytest.mark.parametrize(
        "row",
        [
            {
                "Names": ["/scavengarr"],
                "State": "running",
                "Status": "Up 5 days (unhealthy)",
            },
            {
                "Names": ["/scavengarr"],
                "State": "exited",
                "Status": "Exited (1) 2 min ago",
            },
            {"Names": ["/other"], "State": "running", "Status": "Up 1 hour (healthy)"},
        ],
    )
    def test_anything_else_fails(self, row: dict[str, Any]) -> None:
        assert _mod.container_check([row], "scavengarr").status == "fail"


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


class TestStreams:
    def test_anime_ids_with_their_codes_and_the_movie_pass(self) -> None:
        answers = [
            *_anime_answers(),
            _mod.Answer(_mod.MOVIE_ID, 200, [_stream("x")], 20.0, "m"),
        ]
        records = [
            {"event": "http_request", "request_id": "r0", "path": "/x"},
            {
                "event": "stremio_episode_ref",
                "request_id": "r0",
                "season": 5,
                "episode": 2,
                "absolute": 1089,
            },
            {"event": "http_request", "request_id": "m", "path": "/y"},
        ]

        checks = _mod.stream_checks(answers, records, deadline_s=60)

        assert [c.status for c in checks] == ["pass"] * 4
        assert checks[0].detail == (
            "1 streams, all S05E02; logged; placed S05E02 abs 1089, 3.0 s"
        )
        assert checks[1].detail == (
            "1 streams, all S22E04; not in the log; no placement record (DEBUG), 4.0 s"
        )
        assert checks[3].detail == "1 streams in 20.0 s; logged"

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
            "0 streams, none S05E02 (first: none); not in the log, 1.0 s",
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
        assert checks[0].detail == "no streams, 5.0 s"
        assert checks[1].detail == "1 streams but 61.0 s over the 60 s deadline"


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
