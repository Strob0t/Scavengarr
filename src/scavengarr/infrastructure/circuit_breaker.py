"""Per-plugin circuit breaker to skip consistently failing plugins.

When a plugin accumulates ``failure_threshold`` consecutive failures
(exceptions or timeouts), the breaker opens and subsequent calls are
short-circuited for ``cooldown_seconds``.  After the cooldown, a
single probe request is allowed (half-open state).  If the probe
succeeds the breaker resets; if it fails the cooldown restarts.
"""

from __future__ import annotations

import time
from enum import Enum


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class PluginCircuitBreaker:
    """Track per-plugin failure counts and manage open/closed state.

    Thread-safety note: this class is *not* thread-safe but is safe
    for single-threaded asyncio (no concurrent mutations within one
    event loop tick).
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._states: dict[str, _State] = {}
        self._opened_at: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allow(self, name: str) -> bool:
        """Return ``True`` if *name* is allowed to execute.

        - **CLOSED**: always allowed.
        - **OPEN**: blocked until cooldown expires, then transitions to
          HALF_OPEN and allows a single probe.
        - **HALF_OPEN**: allowed (probe in progress).
        """
        state = self._states.get(name, _State.CLOSED)

        if state == _State.CLOSED:
            return True

        if state == _State.OPEN:
            elapsed = time.monotonic() - self._opened_at.get(name, 0.0)
            if elapsed >= self._cooldown:
                self._states[name] = _State.HALF_OPEN
                return True
            return False

        # HALF_OPEN — allow the probe
        return True

    def record_success(self, name: str) -> None:
        """Record a successful execution — resets the breaker to CLOSED."""
        self._failures.pop(name, None)
        self._states.pop(name, None)
        self._opened_at.pop(name, None)

    def record_failure(self, name: str) -> None:
        """Record a failed execution.

        Increments the consecutive failure counter.  When the counter
        reaches the threshold the breaker opens.  In HALF_OPEN state,
        a single failure re-opens the breaker immediately.
        """
        state = self._states.get(name, _State.CLOSED)

        if state == _State.HALF_OPEN:
            # Probe failed — reopen
            self._states[name] = _State.OPEN
            self._opened_at[name] = time.monotonic()
            return

        count = self._failures.get(name, 0) + 1
        self._failures[name] = count

        if count >= self._threshold:
            self._states[name] = _State.OPEN
            self._opened_at[name] = time.monotonic()

    def state(self, name: str) -> str:
        """Return the current state as a string (for diagnostics)."""
        return self._states.get(name, _State.CLOSED).value

    def reset(self, name: str) -> None:
        """Manually reset *name* back to CLOSED."""
        self.record_success(name)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return a diagnostic snapshot of all tracked plugins."""
        names = set(self._failures) | set(self._states)
        result: dict[str, dict[str, object]] = {}
        for n in sorted(names):
            result[n] = {
                "state": self.state(n),
                "failures": self._failures.get(n, 0),
            }
        return result
