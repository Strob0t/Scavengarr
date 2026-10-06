"""Tests for the telemetry port's no-op default."""

from __future__ import annotations

import pytest

from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort


class TestNoTelemetry:
    def test_is_a_telemetry_port(self) -> None:
        assert isinstance(NO_TELEMETRY, TelemetryPort)

    def test_stage_accepts_outcome_labels_and_attributes(self) -> None:
        with NO_TELEMETRY.stage("plugin_search", plugin="kinoger") as stage:
            stage.outcome = "hits"
            stage.label(plugin="other")
            stage.annotate(results=3, cached=False)

        assert stage.outcome == "hits"

    def test_stages_do_not_share_state(self) -> None:
        with NO_TELEMETRY.stage("plugin_search", plugin="a") as first:
            first.outcome = "hits"
        with NO_TELEMETRY.stage("plugin_search", plugin="b") as second:
            pass

        assert second.outcome is None

    def test_stage_lets_exceptions_through(self) -> None:
        with (
            pytest.raises(ValueError),
            NO_TELEMETRY.stage("hoster_resolve", resolver="voe"),
        ):
            raise ValueError("boom")

    def test_count_and_record_do_nothing(self) -> None:
        NO_TELEMETRY.count("plugin_search", "breaker_open", plugin="kinoger")
        NO_TELEMETRY.record("stremio_streams", 5)
