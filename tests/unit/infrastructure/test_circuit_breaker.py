"""Tests for PluginCircuitBreaker."""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from unittest.mock import patch

from structlog.testing import capture_logs

from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker


class TestOpeningLog:
    def test_an_opening_and_a_reopening_are_logged(self) -> None:
        """One line per opening: the production digest counts them."""
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=0)
        with capture_logs() as logs:
            cb.record_failure("foo")
            cb.record_failure("foo")
            cb.allow("foo")  # half-open probe
            cb.record_failure("foo")  # reopened

        assert [
            (e["event"], e["name"], e["reopened"], e["cooldown_s"]) for e in logs
        ] == [
            ("circuit_breaker_opened", "foo", False, 0),
            ("circuit_breaker_opened", "foo", True, 0),
        ]


class TestInitialState:
    def test_new_plugin_is_allowed(self) -> None:
        cb = PluginCircuitBreaker()
        assert cb.allow("foo") is True

    def test_new_plugin_state_is_closed(self) -> None:
        cb = PluginCircuitBreaker()
        assert cb.state("foo") == "closed"


class TestIsClosed:
    def test_open_breaker_is_not_closed_and_starts_no_probe(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        cb.record_failure("foo")

        assert cb.is_closed("foo") is False
        assert cb.state("foo") == "open"  # allow() would turn it half-open

    def test_closed_until_the_threshold(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2)
        cb.record_failure("foo")
        assert cb.is_closed("foo") is True


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


class TestSingleProbe:
    """Half-open lets one probe through, not every concurrent request."""

    @staticmethod
    def _open_at(cb: PluginCircuitBreaker, now: float) -> None:
        with patch.object(time, "monotonic", return_value=now):
            for _ in range(2):
                cb.record_failure("foo")

    def test_only_one_probe_while_half_open(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self._open_at(cb, 1000.0)

        with patch.object(time, "monotonic", return_value=1060.0):
            assert cb.allow("foo") is True  # the probe
            # concurrent requests wait for its outcome
            assert cb.allow("foo") is False
            assert cb.allow("foo") is False
        assert cb.state("foo") == "half_open"

    def test_lost_probe_is_replaced_after_the_cooldown(self) -> None:
        """A probe that never reports (cancelled, timeout not blamed)."""
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self._open_at(cb, 1000.0)
        with patch.object(time, "monotonic", return_value=1060.0):
            assert cb.allow("foo") is True

        with patch.object(time, "monotonic", return_value=1060.0 + 59):
            assert cb.allow("foo") is False
        with patch.object(time, "monotonic", return_value=1060.0 + 60):
            assert cb.allow("foo") is True  # the new probe

    def test_a_released_probe_lets_the_next_call_probe(self) -> None:
        """A probe without a verdict (a deleted file, a network error) held
        its slot for a whole cooldown, up to an hour (code review,
        2026-10-06)."""
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self._open_at(cb, 1000.0)
        with patch.object(time, "monotonic", return_value=1060.0):
            assert cb.allow("foo") is True
            cb.release("foo")

            assert cb.allow("foo") is True  # the next probe
            assert cb.allow("foo") is False
        assert cb.state("foo") == "half_open"

    def test_release_leaves_other_states_alone(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        cb.release("closed")
        self._open_at(cb, 1000.0)
        with patch.object(time, "monotonic", return_value=1010.0):
            cb.release("foo")

            assert cb.allow("foo") is False
        assert cb.state("closed") == "closed"
        assert cb.state("foo") == "open"

    def test_probe_success_lets_everyone_through(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        self._open_at(cb, 1000.0)
        with patch.object(time, "monotonic", return_value=1060.0):
            assert cb.allow("foo") is True
            cb.record_success("foo")
            assert cb.allow("foo") is True
            assert cb.allow("foo") is True


def _at(now: float) -> AbstractContextManager[object]:
    return patch.object(time, "monotonic", return_value=now)


def _open_with_120s_cooldown() -> PluginCircuitBreaker:
    """'foo' open since 1060 with its cooldown doubled to 120 s."""
    cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    with _at(1000.0):
        cb.record_failure("foo")
        cb.record_failure("foo")
    with _at(1060.0):
        assert cb.allow("foo") is True
        cb.record_failure("foo")
    return cb


class TestStateExport:
    """Open breakers outlive a restart (HosterStateStore): exported with the
    cooldown left, restored with it shortened by the downtime."""

    def test_the_cooldown_left_shrinks_by_the_downtime(self) -> None:
        """40 s of 120 s left, 15 s down: open for 25 s, and a failed probe
        doubles the cooldown to 240 s (openspec persist-resolver-state)."""
        with _at(1140.0):
            entries = _open_with_120s_cooldown().export_state()
        assert entries == [
            {"name": "foo", "remaining_cooldown": 40.0, "cooldown": 120.0}
        ]

        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        with _at(50.0):
            assert cb.import_state(entries, age_s=15.0) == 1
        assert cb.state("foo") == "open"
        assert cb.is_closed("foo") is False
        with _at(74.0):
            assert cb.allow("foo") is False
        with _at(75.0):
            assert cb.allow("foo") is True  # the probe
            cb.record_failure("foo")
        with _at(75.0 + 239):
            assert cb.allow("foo") is False
        with _at(75.0 + 240):
            assert cb.allow("foo") is True

    def test_closed_breakers_are_not_exported(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2)
        cb.record_failure("below")  # under the threshold
        cb.record_failure("healed")
        cb.record_failure("healed")
        cb.record_success("healed")

        assert cb.export_state() == []

    def test_a_cooldown_that_ran_out_while_down_leaves_the_probe_due(self) -> None:
        """Not closed: a closed breaker would let every call through and
        forget the doubled cooldown."""
        with _at(1140.0):
            entries = _open_with_120s_cooldown().export_state()

        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        with _at(50.0):
            cb.import_state(entries, age_s=600.0)
            assert cb.allow("foo") is True  # the probe
            assert cb.allow("foo") is False  # one at a time
            cb.record_failure("foo")
        with _at(50.0 + 239):
            assert cb.allow("foo") is False

    def test_a_half_open_probe_ends_with_the_process(self) -> None:
        """The probe in flight at the export is lost: the next call probes."""
        cb = _open_with_120s_cooldown()
        with _at(1180.0):
            assert cb.allow("foo") is True  # half-open, probe in flight
            entries = cb.export_state()
        assert entries == [
            {"name": "foo", "remaining_cooldown": 0.0, "cooldown": 120.0}
        ]

        restored = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        with _at(50.0):
            restored.import_state(entries, age_s=0.0)
            assert restored.allow("foo") is True

    def test_a_restored_cooldown_keeps_to_the_cap(self) -> None:
        """A snapshot of a run with a longer maximum cooldown."""
        entries = [{"name": "foo", "remaining_cooldown": 7000.0, "cooldown": 7200.0}]
        cb = PluginCircuitBreaker(
            failure_threshold=2, cooldown_seconds=60, max_cooldown_seconds=3600
        )
        with _at(0.0):
            cb.import_state(entries, age_s=0.0)
        with _at(3599.0):
            assert cb.allow("foo") is False
        with _at(3600.0):
            assert cb.allow("foo") is True

    def test_a_restored_breaker_reports_its_failure_streak(self) -> None:
        """Open means at least the threshold of failures (snapshot())."""
        cb = PluginCircuitBreaker(failure_threshold=5)
        cb.import_state(
            [{"name": "foo", "remaining_cooldown": 30.0, "cooldown": 60.0}], age_s=0.0
        )
        assert cb.snapshot() == {"foo": {"state": "open", "failures": 5}}


class TestChanges:
    """What a snapshot holds changed: a breaker opened, reopened or closed."""

    def test_opening_reopening_and_closing_count(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2, cooldown_seconds=0)
        cb.record_failure("foo")
        assert cb.changes == 0  # below the threshold
        cb.record_failure("foo")
        assert cb.changes == 1  # opened
        assert cb.allow("foo") is True  # half-open: the export says the same
        assert cb.changes == 1
        cb.record_failure("foo")
        assert cb.changes == 2  # reopened
        cb.allow("foo")
        cb.record_success("foo")
        assert cb.changes == 3  # closed

    def test_success_of_a_closed_breaker_changes_nothing(self) -> None:
        cb = PluginCircuitBreaker(failure_threshold=2)
        cb.record_failure("foo")
        cb.record_success("foo")
        assert cb.changes == 0
