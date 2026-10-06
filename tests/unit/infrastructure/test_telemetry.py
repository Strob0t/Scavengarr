"""Tests for the Prometheus telemetry (stages, counts, values, JSON snapshot)."""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from structlog.testing import capture_logs

from scavengarr.domain.ports.telemetry import TelemetryPort
from scavengarr.infrastructure.telemetry import (
    LAG_WINDOW,
    Telemetry,
    monitor_loop_lag,
)


def _sample(t: Telemetry, name: str, **labels: str) -> float | None:
    return t.registry.get_sample_value(name, labels)


class TestStage:
    def test_is_a_telemetry_port(self) -> None:
        assert isinstance(Telemetry(), TelemetryPort)

    def test_records_duration_and_outcome(self) -> None:
        t = Telemetry()
        with t.stage("plugin_search", plugin="kinoger") as stage:
            time.sleep(0.01)
            stage.outcome = "hits"

        assert (
            _sample(t, "scavengarr_plugin_search_seconds_count", plugin="kinoger") == 1
        )
        seconds = _sample(t, "scavengarr_plugin_search_seconds_sum", plugin="kinoger")
        assert seconds is not None and seconds >= 0.01
        total = _sample(
            t, "scavengarr_plugin_search_total", plugin="kinoger", outcome="hits"
        )
        assert total == 1

    def test_outcome_defaults_to_ok(self) -> None:
        t = Telemetry()
        with t.stage("stremio_phase", phase="search"):
            pass

        assert _sample(
            t, "scavengarr_stremio_phase_total", phase="search", outcome="ok"
        )

    async def test_cancelled_stage_is_cut(self) -> None:
        t = Telemetry()

        async def search() -> None:
            with t.stage("plugin_search", plugin="slow"):
                await asyncio.sleep(10)

        task = asyncio.create_task(search())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert _sample(
            t, "scavengarr_plugin_search_total", plugin="slow", outcome="cut"
        )
        assert _sample(t, "scavengarr_plugin_search_seconds_count", plugin="slow") == 1

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [(TimeoutError(), "timeout"), (ValueError("boom"), "error")],
    )
    def test_exception_sets_outcome_and_propagates(
        self, error: Exception, outcome: str
    ) -> None:
        t = Telemetry()
        with (
            pytest.raises(type(error)),
            t.stage("hoster_resolve", resolver="voe"),
        ):
            raise error

        assert _sample(
            t, "scavengarr_hoster_resolve_total", resolver="voe", outcome=outcome
        )

    def test_outcome_set_by_the_code_wins_over_the_exception(self) -> None:
        t = Telemetry()
        with pytest.raises(ValueError), t.stage("hoster_resolve", resolver="voe") as s:
            s.outcome = "unplayable"
            raise ValueError("late")

        assert _sample(
            t, "scavengarr_hoster_resolve_total", resolver="voe", outcome="unplayable"
        )

    def test_label_set_while_running(self) -> None:
        t = Telemetry()
        with t.stage("stremio_request", source="none") as stage:
            stage.label(source="cache")
            stage.outcome = "streams"

        assert _sample(t, "scavengarr_stremio_request_seconds_count", source="cache")
        assert not _sample(t, "scavengarr_stremio_request_seconds_count", source="none")

    def test_annotations_are_not_metric_labels(self) -> None:
        t = Telemetry()
        with t.stage("plugin_search", plugin="kinoger") as stage:
            stage.annotate(results=3)
            stage.outcome = "hits"

        assert _sample(
            t, "scavengarr_plugin_search_total", plugin="kinoger", outcome="hits"
        )

    def test_wrong_label_name_fails(self) -> None:
        t = Telemetry()
        with pytest.raises(ValueError), t.stage("plugin_search", hoster="voe"):
            pass


class TestCountAndRecord:
    def test_count_has_no_duration(self) -> None:
        t = Telemetry()
        t.count("plugin_search", "breaker_open", plugin="kinoking")

        assert _sample(
            t,
            "scavengarr_plugin_search_total",
            plugin="kinoking",
            outcome="breaker_open",
        )
        assert (
            _sample(t, "scavengarr_plugin_search_seconds_count", plugin="kinoking")
            is None
        )

    def test_record_counter(self) -> None:
        t = Telemetry()
        t.record("plugin_results", 12, plugin="kinoger")
        t.record("plugin_results", 3, plugin="kinoger")

        assert _sample(t, "scavengarr_plugin_results_total", plugin="kinoger") == 15

    def test_record_histogram(self) -> None:
        t = Telemetry()
        t.record("stremio_streams", 5)

        assert _sample(t, "scavengarr_stremio_streams_count") == 1
        assert _sample(t, "scavengarr_stremio_streams_bucket", le="5.0") == 1
        assert _sample(t, "scavengarr_stremio_streams_bucket", le="3.0") == 0

    def test_record_without_its_label_fails(self) -> None:
        t = Telemetry()
        with pytest.raises(ValueError):
            t.record("hls_proxy_bytes", 100)


class TestRender:
    def test_text_format_with_standard_families(self) -> None:
        t = Telemetry()
        with t.stage("plugin_search", plugin="kinoger") as stage:
            stage.outcome = "hits"

        text = t.render().decode()

        assert (
            'scavengarr_plugin_search_total{outcome="hits",plugin="kinoger"} 1.0'
            in text
        )
        assert "scavengarr_build_info{version=" in text
        assert "python_info" in text
        assert "_created" not in text

    def test_instances_do_not_share_metrics(self) -> None:
        first, second = Telemetry(), Telemetry()
        first.count("plugin_search", "skipped", plugin="a")

        assert (
            _sample(
                second, "scavengarr_plugin_search_total", plugin="a", outcome="skipped"
            )
            is None
        )


class TestPluginSnapshot:
    """``/api/v1/stats/metrics`` keeps its plugin statistics."""

    def test_snapshot_from_the_plugin_search_metrics(self) -> None:
        t = Telemetry()
        for outcome in ("hits", "hits", "hits", "empty", "cut"):
            with t.stage("plugin_search", plugin="kinoger") as stage:
                stage.outcome = outcome
        t.record("plugin_results", 7, plugin="kinoger")
        t.count("plugin_search", "breaker_open", plugin="kinoger")

        entry = t.snapshot()["plugins"]["kinoger"]

        assert entry["searches"] == 5
        assert entry["successes"] == 4
        assert entry["failures"] == 1
        assert entry["total_results"] == 7
        assert isinstance(entry["avg_duration_ms"], float)

    def test_plugins_sorted_and_only_searched_ones(self) -> None:
        t = Telemetry()
        for name in ("zeta", "alpha"):
            with t.stage("plugin_search", plugin=name) as stage:
                stage.outcome = "empty"
        t.count("plugin_search", "unreachable", plugin="mid")

        assert list(t.snapshot()["plugins"]) == ["alpha", "zeta"]

    def test_snapshot_includes_uptime(self) -> None:
        uptime = Telemetry().snapshot()["uptime_seconds"]
        assert isinstance(uptime, float) and uptime >= 0


class TestEventLoopLag:
    """How late a periodic timer fires: CPU-bound work on the event loop
    (parsing, logging) delays every callback, timers and deadlines included."""

    def test_snapshot_without_samples(self) -> None:
        assert Telemetry().snapshot()["event_loop"] == {"samples": 0}

    def test_snapshot_percentiles(self) -> None:
        t = Telemetry()
        for lag in range(100):  # 0 … 99 ms
            t.record_loop_lag(float(lag))

        assert t.snapshot()["event_loop"] == {
            "samples": 100,
            "lag_ms_p50": 50.0,
            "lag_ms_p99": 99.0,
            "lag_ms_max": 99.0,
        }

    def test_window_keeps_the_latest_samples(self) -> None:
        t = Telemetry()
        for _ in range(LAG_WINDOW):
            t.record_loop_lag(500.0)
        for _ in range(LAG_WINDOW):
            t.record_loop_lag(1.0)

        assert t.snapshot()["event_loop"]["lag_ms_max"] == 1.0

    def test_lag_goes_into_the_histogram(self) -> None:
        t = Telemetry()
        t.record_loop_lag(30.0)

        assert _sample(t, "scavengarr_event_loop_lag_seconds_count") == 1
        assert _sample(t, "scavengarr_event_loop_lag_seconds_bucket", le="0.05") == 1
        assert _sample(t, "scavengarr_event_loop_lag_seconds_bucket", le="0.025") == 0

    async def test_monitor_records_a_blocked_loop(self) -> None:
        t = Telemetry()
        task = asyncio.create_task(monitor_loop_lag(t, interval=0.01, warn_ms=1e9))
        await asyncio.sleep(0.02)
        time.sleep(0.05)  # CPU-bound work holding the loop
        await asyncio.sleep(0.03)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        lag = t.snapshot()["event_loop"]
        assert lag["samples"] >= 2
        assert lag["lag_ms_max"] >= 30.0

    async def test_monitor_warns_on_a_long_stall(self) -> None:
        t = Telemetry()
        with capture_logs() as logs:
            task = asyncio.create_task(monitor_loop_lag(t, interval=0.01, warn_ms=20))
            await asyncio.sleep(0.02)
            time.sleep(0.05)
            await asyncio.sleep(0.03)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert any(e["event"] == "event_loop_lag" for e in logs)
