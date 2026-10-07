"""Tests for scripts/probes/domains.py: the step lines, the rule, the record."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import httpx

_PROBE = Path(__file__).resolve().parents[3] / "scripts/probes/domains.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("domains_probe", _PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclass resolves its annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers, request=httpx.Request("HEAD", "https://a.example/")
    )


class TestSteps:
    def test_http_step_names_status_challenge_and_edge_without_path(self) -> None:
        resp = _response(522, {"server": "cloudflare"})

        line = probe.http_step("GET", resp, time.monotonic())

        assert line.startswith("GET 522 [cloudflare] ")
        assert line.endswith(" ms")
        assert "/" not in line

    def test_http_step_shows_the_redirect_host_only(self) -> None:
        resp = _response(200)
        resp.history = [_response(301)]
        resp.request = httpx.Request("HEAD", "https://www.b.example/some/path")

        line = probe.http_step("HEAD", resp, time.monotonic())

        assert "→ www.b.example" in line
        assert "some" not in line

    def test_get_step_reads_the_challenge_page(self) -> None:
        resp = httpx.Response(
            403,
            text="<title>Just a moment...</title>",
            request=httpx.Request("GET", "https://a.example/"),
        )

        line = probe.http_step("GET", resp, time.monotonic())

        assert line.startswith("GET 403 (cloudflare_page) ")


class TestAnswers:
    def test_below_400_answers(self) -> None:
        assert probe.answers(_response(302)) is True

    def test_the_domain_checks_rule_decides_above(self) -> None:
        # an error page is the site, a 522 from the edge is not
        assert probe.answers(_response(404)) is True
        assert probe.answers(_response(522, {"server": "cloudflare"})) is False

    def test_a_challenge_page_answers(self) -> None:
        # kinoger: 403 with the challenge; below 500 by the rule
        assert probe.answers(_response(403)) is True


class TestHistory:
    def test_lines_name_the_record_and_its_days(self) -> None:
        report = {
            "plugins": {
                "movie4k": {
                    "last_result_day": None,
                    "unreachable_share": {"30": 1.0, "90": None},
                    "days": {"2026-10-07": {"checks": 14, "unreachable": 14}},
                }
            }
        }

        lines = probe.history_lines(report, ["movie4k", "megakino_to"])

        assert lines == [
            "- movie4k: last result day none; unreachable share 30 d 100%, 90 d -",
            "  - 2026-10-07: checks 14, unreachable 14",
            "- megakino_to: no record",
        ]

    def test_snapshot_becomes_the_endpoints_shape(self) -> None:
        snapshot = {
            "version": 1,
            "plugins": {
                "sto": {
                    "2026-10-06": {"searches": 2, "results": 0},
                    "2026-10-05": {"searches": 3, "results": 4},
                }
            },
        }

        report = probe.report_of_snapshot(snapshot)

        assert report["plugins"]["sto"]["last_result_day"] == "2026-10-05"
        assert list(report["plugins"]["sto"]["days"]) == ["2026-10-05", "2026-10-06"]
        assert probe.report_of_snapshot(None) == {"plugins": {}}
