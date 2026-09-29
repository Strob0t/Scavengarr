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


class TestCooldownBackoff:
    """Every failed half-open trial doubles the cooldown (capped)."""

    @staticmethod
    def _open(cb: PluginCircuitBreaker, now: float) -> None:
        with patch.object(time, "monotonic", return_value=now):
            for _ in range(2):
                cb.record_failure("foo")

    @staticmethod
    def _trial_fails(cb: PluginCircuitBreaker, at: float) -> None:
        with patch.object(time, "monotonic", return_value=at):
            assert cb.allow("foo") is True  # half-open trial
            cb.record_failure("foo")

    def test_cooldown_doubles_after_failed_trials(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self._open(cb, 1000.0)
        self._trial_fails(cb, 1060.0)  # cooldown now 120 s

        with patch.object(time, "monotonic", return_value=1060.0 + 119):
            assert cb.allow("foo") is False
        self._trial_fails(cb, 1060.0 + 120)  # cooldown now 240 s

        with patch.object(time, "monotonic", return_value=1180.0 + 239):
            assert cb.allow("foo") is False
        with patch.object(time, "monotonic", return_value=1180.0 + 240):
            assert cb.allow("foo") is True

    def test_cooldown_is_capped(self) -> None:
        cb = PluginCircuitBreaker(
            failure_threshold=2, cooldown_seconds=60, max_cooldown_seconds=100
        )
        self._open(cb, 0.0)
        self._trial_fails(cb, 60.0)  # 120 → capped at 100
        with patch.object(time, "monotonic", return_value=60.0 + 100):
            assert cb.allow("foo") is True

    def test_success_resets_the_backoff(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self._open(cb, 0.0)
        self._trial_fails(cb, 60.0)
        with patch.object(time, "monotonic", return_value=180.0):
            assert cb.allow("foo") is True
            cb.record_success("foo")
        self._open(cb, 200.0)
        with patch.object(time, "monotonic", return_value=260.0):
            assert cb.allow("foo") is True  # back to the base cooldown
