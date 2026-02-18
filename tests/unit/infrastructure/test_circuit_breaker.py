"""Tests for PluginCircuitBreaker."""

from __future__ import annotations

import time
from unittest.mock import patch

from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker


class TestInitialState:
    def test_new_plugin_is_allowed(self) -> None:
        cb = PluginCircuitBreaker()
        assert cb.allow("foo") is True

    def test_new_plugin_state_is_closed(self) -> None:
        cb = PluginCircuitBreaker()
        assert cb.state("foo") == "closed"


class TestClosedState:
    def test_failures_below_threshold_stay_closed(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=3)
        cb.record_failure("foo")
        cb.record_failure("foo")
        assert cb.allow("foo") is True
        assert cb.state("foo") == "closed"

    def test_success_resets_failure_count(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=3)
        cb.record_failure("foo")
        cb.record_failure("foo")
        cb.record_success("foo")
        cb.record_failure("foo")
        # Only 1 failure after reset — still closed
        assert cb.allow("foo") is True
        assert cb.state("foo") == "closed"


class TestOpenState:
    def test_opens_at_threshold(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure("foo")
        assert cb.state("foo") == "open"
        assert cb.allow("foo") is False

    def test_blocked_during_cooldown(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb.record_failure("foo")
        cb.record_failure("foo")
        assert cb.allow("foo") is False

    def test_transitions_to_half_open_after_cooldown(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=10)
        cb.record_failure("foo")
        cb.record_failure("foo")

        # Simulate time passing
        with patch.object(time, "monotonic", return_value=time.monotonic() + 11):
            assert cb.allow("foo") is True
            assert cb.state("foo") == "half_open"


class TestHalfOpenState:
    def test_success_closes_breaker(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=0)
        cb.record_failure("foo")
        cb.record_failure("foo")
        # Cooldown = 0 → immediately half-open
        assert cb.allow("foo") is True
        cb.record_success("foo")
        assert cb.state("foo") == "closed"
        assert cb.allow("foo") is True

    def test_failure_reopens_breaker(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb.record_failure("foo")
        cb.record_failure("foo")
        # Simulate cooldown expiry to enter half-open
        with patch.object(time, "monotonic", return_value=time.monotonic() + 61):
            assert cb.allow("foo") is True  # half-open
        cb.record_failure("foo")
        assert cb.state("foo") == "open"
        # Fresh cooldown started — blocked again
        assert cb.allow("foo") is False


class TestIsolation:
    def test_plugins_are_independent(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2)
        cb.record_failure("foo")
        cb.record_failure("foo")
        assert cb.allow("foo") is False
        assert cb.allow("bar") is True

    def test_success_only_affects_named_plugin(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2)
        cb.record_failure("foo")
        cb.record_failure("foo")
        cb.record_success("bar")
        assert cb.allow("foo") is False


class TestReset:
    def test_manual_reset_closes_breaker(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2)
        cb.record_failure("foo")
        cb.record_failure("foo")
        assert cb.state("foo") == "open"
        cb.reset("foo")
        assert cb.state("foo") == "closed"
        assert cb.allow("foo") is True


class TestSnapshot:
    def test_empty_snapshot(self) -> None:
        cb = PluginCircuitBreaker()
        assert cb.snapshot() == {}

    def test_snapshot_shows_state_and_failures(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=3)
        cb.record_failure("alpha")
        cb.record_failure("beta")
        cb.record_failure("beta")
        cb.record_failure("beta")
        snap = cb.snapshot()
        assert snap["alpha"]["state"] == "closed"
        assert snap["alpha"]["failures"] == 1
        assert snap["beta"]["state"] == "open"
        assert snap["beta"]["failures"] == 3
