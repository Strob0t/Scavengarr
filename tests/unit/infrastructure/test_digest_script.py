"""Tests for scripts/digest.py, the data behind ``prodctl.py digest``."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    try:
        return importlib.import_module("digest")
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()

# 2026-10-07 12:00:00 UTC; the window is the day before
_NOW = 1791374400.0
_DAY_S = 86400.0


def _line(**fields: Any) -> str:
    return "2026-10-07T08:00:00.000000000Z " + json.dumps(
        {"timestamp": "2026-10-07T08:00:00Z", "level": "info", **fields}
    )


def _stream_request(
    rid: str,
    *events: dict[str, Any],
    streams: int | None,
    duration_ms: float,
    stream_id: str = "movie/tt1",
) -> list[str]:
    lines = [_line(event="stremio_stream_request", request_id=rid, imdb_id="tt1")]
    lines.extend(_line(request_id=rid, **event) for event in events)
    if streams is not None:
        lines.append(
            _line(
                event="stremio_stream_response",
                request_id=rid,
                streams_returned=streams,
            )
        )
    lines.append(
        _line(
            event="http_request",
            request_id=rid,
            path=f"/api/v1/stremio/stream/{stream_id}.json",
            duration_ms=duration_ms,
        )
    )
    return lines


_LOG = "\n".join(
    [
        *_stream_request(
            "r1", {"event": "stremio_search_start"}, streams=5, duration_ms=13400
        ),
        *_stream_request(
            "r2",
            {"event": "stremio_search_cache_hit", "stale": False},
            streams=4,
            duration_ms=70,
        ),
        *_stream_request(
            "r3",
            {"event": "stremio_search_cache_hit", "stale": True},
            streams=0,
            duration_ms=1600,
        ),
        *_stream_request("r4", streams=3, duration_ms=9900),
        *_stream_request(
            "r5", {"event": "stremio_title_not_found"}, streams=0, duration_ms=50
        ),
        _line(event="stremio_stream_request", request_id="r6"),
        # The anime catalogs' ids and a TMDB id, told apart by the path
        *_stream_request(
            "r7", streams=1, duration_ms=200, stream_id="series/kitsu:12345:2"
        ),
        *_stream_request("r8", streams=0, duration_ms=300, stream_id="movie/tmdb:603"),
        _line(event="http_request", path="/api/v1/healthz", duration_ms=1.1),
        _line(event="http_request", path="/api/v1/readyz", duration_ms=1.2),
        _line(event="hoster_resolve_timeout", hoster="dropload", level="warning"),
        _line(event="circuit_breaker_opened", name="filemoon", level="warning"),
        _line(event="stremio_plugin_circuit_open", plugin="kinoger"),
        _line(event="hoster_resolve_circuit_open", hoster="dropload"),
        _line(event="hoster_resolve_circuit_open", hoster="dropload"),
        # asyncio names the task on the event's second line
        _line(
            event="Task exception was never retrieved\nfuture: <Task-1 ...>",
            level="error",
            logger="asyncio",
            exception="Traceback (most recent call last):\nRuntimeError: aclose()",
        ),
        _line(
            event="Task exception was never retrieved\nfuture: <Task-2 ...>",
            level="error",
            logger="asyncio",
            exception="Traceback (most recent call last):\nRuntimeError: aclose()",
        ),
        "2026-10-07T08:00:00.000000000Z not a record",
    ]
)

_METRICS = "\n".join(
    [
        "# HELP scavengarr_hoster_resolve_total Hoster resolutions by outcome",
        'scavengarr_hoster_resolve_total{outcome="stream",resolver="voe"} 93.0',
        'scavengarr_hoster_resolve_total{outcome="dead",resolver="direct"} 81.0',
        'scavengarr_hoster_resolve_seconds_count{resolver="voe"} 93.0',
        'scavengarr_hoster_resolve_seconds_sum{resolver="voe"} 111.6',
        'scavengarr_hoster_resolve_seconds_count{resolver="direct"} 81.0',
        'scavengarr_hoster_resolve_seconds_sum{resolver="direct"} 24.3',
        'scavengarr_circuit_breaker_open{breaker="hoster",name="filemoon",state="open"}'
        " 1.0",
        'scavengarr_build_info{built="2026-10-06",commit="abc1234",version="0.3.0"}'
        " 1.0",
        "process_start_time_seconds 1791300000.0",
        "garbage line",
    ]
)

_RECORD = {
    "today": "2026-10-07",
    "window_days": 180,
    "plugins": {
        "kinoger": {
            "last_result_day": "2026-10-06",
            "unreachable_share": {"30": 0.1, "90": None, "180": None},
            "days": {
                "2026-10-06": {"searches": 10, "results": 3},
                "2026-10-07": {
                    "searches": 5,
                    "timeouts": 2,
                    "checks": 3,
                    "unreachable": 1,
                },
            },
        },
        "movie4k": {
            "last_result_day": None,
            "unreachable_share": {"30": None, "90": None, "180": None},
            "days": {"2026-09-01": {"searches": 4}},
        },
    },
}


def _digest(record: Any = _RECORD, since_s: float = _DAY_S) -> dict[str, Any]:
    return _mod.digest(
        container="scavengarr",
        since_s=since_s,
        log_text=_LOG,
        metrics_text=_METRICS,
        plugin_record=record,
        now=_NOW,
    )


class TestParsing:
    def test_records_skip_health_checks_and_other_lines(self) -> None:
        records = _mod.parse_records(_LOG)

        events = [r["event"] for r in records]
        assert "not a record" not in events
        assert all(
            r.get("path") not in ("/api/v1/healthz", "/api/v1/readyz") for r in records
        )
        assert events.count("http_request") == 7

    def test_metrics_samples_with_labels(self) -> None:
        samples = _mod.parse_metrics(_METRICS)

        assert ("process_start_time_seconds", {}, 1791300000.0) in samples
        assert (
            "scavengarr_hoster_resolve_total",
            {"outcome": "stream", "resolver": "voe"},
            93.0,
        ) in samples
        assert len(samples) == 9


class TestSections:
    def test_requests_by_source(self) -> None:
        req = _digest()["requests"]

        assert (req["answered"], req["unanswered"]) == (7, 1)
        assert list(req["by_source"]) == ["search", "joined", "stale", "cache", "none"]
        assert req["by_source"]["search"] == {
            "count": 1,
            "p50_ms": 13400.0,
            "p90_ms": 13400.0,
            "streams_per_answer": 5.0,
            "without_streams": 0,
        }
        assert req["by_source"]["stale"]["without_streams"] == 1
        assert req["by_source"]["cache"]["p50_ms"] == 70.0

    def test_requests_by_id_scheme(self) -> None:
        """Which catalogs the players use: the id scheme of the request path
        (r6 has no access record in the window, so no scheme)."""
        req = _digest()["requests"]

        assert req["by_scheme"] == {"tt": 5, "kitsu": 1, "tmdb": 1}

    def test_id_scheme_of_a_path(self) -> None:
        scheme = _mod._id_scheme
        assert scheme("/api/v1/stremio/stream/movie/tt0133093.json") == "tt"
        assert scheme("/api/v1/stremio/stream/series/kitsu:12345:2.json") == "kitsu"
        assert scheme("/api/v1/stremio/stream/movie/tmdb:603.json") == "tmdb"
        assert scheme("/api/v1/stremio/stream/movie/abc.json") == "other"
        assert scheme("/api/v1/stremio/stream/movie/") == "other"

    def test_percentiles_by_nearest_rank(self) -> None:
        values = [float(v) for v in range(1, 11)]

        assert _mod._percentile(values, 0.5) == 5.0
        assert _mod._percentile(values, 0.9) == 9.0
        assert _mod._percentile([], 0.5) is None

    def test_plugins_from_the_record_window(self) -> None:
        plugins = _digest()["plugins"]

        assert plugins["first_day"] == "2026-10-06"
        kinoger, movie4k = plugins["plugins"]
        assert kinoger["plugin"] == "kinoger"
        assert (kinoger["searches"], kinoger["results"], kinoger["timeouts"]) == (
            15,
            3,
            2,
        )
        assert (kinoger["checks"], kinoger["unreachable"]) == (3, 1)
        assert kinoger["days_since_result"] == 1
        assert kinoger["unreachable_share"] == {"30": 0.1, "90": None, "180": None}
        # Its one day lies before the window
        assert (movie4k["searches"], movie4k["last_result_day"]) == (0, None)

    def test_plugins_without_the_record(self) -> None:
        plugins = _digest(record=None)["plugins"]

        assert plugins == {"available": False, "plugins": []}

    def test_hosters_from_the_metrics(self) -> None:
        voe, direct = _digest()["hosters"]

        assert voe == {
            "resolver": "voe",
            "resolutions": 93,
            "mean_s": 1.2,
            "outcomes": {"stream": 93},
        }
        assert (direct["resolutions"], direct["mean_s"]) == (81, 0.3)

    def test_breakers_open_opened_and_skipped(self) -> None:
        breakers = _digest()["breakers"]

        assert breakers["open"] == [
            {"breaker": "hoster", "name": "filemoon", "state": "open"}
        ]
        assert breakers["opened"] == [{"name": "filemoon", "openings": 1}]
        assert breakers["skipped"] == [
            {"breaker": "hoster", "name": "dropload", "skipped": 2},
            {"breaker": "plugin", "name": "kinoger", "skipped": 1},
        ]

    def test_noise_and_errors(self) -> None:
        data = _digest()

        assert data["noise"][0]["event"] == "stremio_stream_request"
        assert data["noise"][0]["count"] == 8
        assert data["errors"] == {
            "count": 2,
            "top": [
                {
                    "message": "Task exception was never retrieved (asyncio):"
                    " RuntimeError: aclose()",
                    "count": 2,
                }
            ],
        }

    def test_process_and_window(self) -> None:
        data = _digest()

        assert data["process"] == {
            "version": "0.3.0",
            "commit": "abc1234",
            "uptime_s": 74400,
        }
        assert data["log_records"] == 33
        json.dumps(data)


class TestRender:
    def test_the_report_has_every_section(self) -> None:
        text = _mod.render(_digest())

        assert text.startswith("# scavengarr: the last 24.0 h\n")
        for heading in (
            "Requests",
            "Plugins",
            "Hosters",
            "Breakers",
            "Noise",
            "Errors",
        ):
            assert f"\n## {heading}\n" in text
        assert "| search | 1 | 13.40 s | 13.40 s | 5.0 | 0 |" in text
        assert "\nBy id scheme: tt 5, kitsu 1, tmdb 1\n" in text
        assert "| kinoger | 15 | 3 | 2 | 0 | 3 | 1 | 1 d ago | 10%/–/– |" in text
        assert "| voe | 93 | 1.2 s | stream 93 |" in text
        assert "Not closed now: filemoon (hoster, open)" in text
        assert "Opened in the window: filemoon ×1" in text
        assert "2 at error and above" in text
        assert (
            "| Task exception was never retrieved (asyncio):"
            " RuntimeError: aclose() | 2 |" in text
        )

    def test_without_the_record_and_metrics(self) -> None:
        data = _mod.digest(
            container="scavengarr",
            since_s=900,
            log_text="",
            metrics_text="",
            plugin_record=None,
            now=_NOW,
        )
        text = _mod.render(data)

        assert "# scavengarr: the last 15 min" in text
        assert "no metrics" in text
        assert "The long-term record is not available" in text
        assert "Every breaker is closed now." in text
