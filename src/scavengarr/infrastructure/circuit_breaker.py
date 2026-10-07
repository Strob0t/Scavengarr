"""Circuit breaker to skip consistently failing plugins or hosters.

Keys are plugins per category (Stremio plugin search) or hoster resolvers
(``HosterResolverRegistry``). When a key accumulates ``failure_threshold``
consecutive failures (exceptions or timeouts), the breaker opens and
subsequent calls are short-circuited for ``cooldown_seconds``.  After the cooldown, a
single probe request is allowed (half-open state); concurrent calls
stay blocked until it reports.  If the probe succeeds the breaker
resets; if it fails the breaker reopens with twice the cooldown.  A
probe that never reports is presumed lost after the cooldown.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger(__name__)


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
        max_cooldown_seconds: float = 3600.0,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._max_cooldown = max_cooldown_seconds
        # Per-plugin cooldown: doubles with every failed half-open trial, so
        # a plugin that stays down stops costing requests their full timeout
        # (Stremio requests are minutes apart, a fixed 60 s is always over).
        self._cooldowns: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._states: dict[str, _State] = {}
        self._opened_at: dict[str, float] = {}
        # Start of the half-open probe in flight
        self._probe_started: dict[str, float] = {}
        # Breakers opened, reopened or closed (export_state's content)
        self._changes = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allow(self, name: str) -> bool:
        """Return ``True`` if *name* is allowed to execute.

        - **CLOSED**: always allowed.
        - **OPEN**: blocked until cooldown expires, then transitions to
          HALF_OPEN and allows a single probe.
        - **HALF_OPEN**: blocked while the probe is in flight; a probe that
          has not reported within the cooldown (cancelled, or a timeout
          the caller did not count) is replaced by the next call.
        """
        state = self._states.get(name, _State.CLOSED)

        if state == _State.CLOSED:
            return True

        now = time.monotonic()
        cooldown = self._cooldowns.get(name, self._cooldown)
        if state == _State.OPEN:
            if now - self._opened_at.get(name, 0.0) < cooldown:
                return False
            self._states[name] = _State.HALF_OPEN
        elif now - self._probe_started.get(name, 0.0) < cooldown:
            return False
        self._probe_started[name] = now
        return True

    def record_success(self, name: str) -> None:
        """Record a successful execution — resets the breaker to CLOSED."""
        self._failures.pop(name, None)
        if self._states.pop(name, None) is not None:
            self._changes += 1
        self._opened_at.pop(name, None)
        self._cooldowns.pop(name, None)
        self._probe_started.pop(name, None)

    def record_failure(self, name: str) -> None:
        """Record a failed execution.

        Increments the consecutive failure counter.  When the counter
        reaches the threshold the breaker opens.  In HALF_OPEN state,
        a single failure re-opens the breaker immediately.
        """
        state = self._states.get(name, _State.CLOSED)

        if state == _State.HALF_OPEN:
            # Probe failed — reopen with twice the cooldown (capped)
            self._probe_started.pop(name, None)
            self._states[name] = _State.OPEN
            self._opened_at[name] = time.monotonic()
            self._cooldowns[name] = min(
                self._cooldowns.get(name, self._cooldown) * 2, self._max_cooldown
            )
            self._changes += 1
            self._log_opened(name, reopened=True)
            return

        count = self._failures.get(name, 0) + 1
        self._failures[name] = count

        if count >= self._threshold:
            self._states[name] = _State.OPEN
            self._opened_at[name] = time.monotonic()
            self._changes += 1
            self._log_opened(name, reopened=False)

    def _log_opened(self, name: str, *, reopened: bool) -> None:
        """One line per opening (the production digest counts them)."""
        log.warning(
            "circuit_breaker_opened",
            name=name,
            reopened=reopened,
            cooldown_s=round(self._cooldowns.get(name, self._cooldown)),
        )

    def release(self, name: str) -> None:
        """End a half-open probe that gave no verdict (a deleted file, a
        failed request): the next call probes instead of waiting out the
        cooldown. Other states stay as they are."""
        if self._states.get(name) == _State.HALF_OPEN:
            self._probe_started.pop(name, None)

    def is_closed(self, name: str) -> bool:
        """Whether *name* runs normally (no failure streak); unlike
        :meth:`allow` it never starts a half-open probe."""
        return self._states.get(name, _State.CLOSED) == _State.CLOSED

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

    # ------------------------------------------------------------------
    # State across restarts (HosterStateStore)
    # ------------------------------------------------------------------

    @property
    def changes(self) -> int:
        """How often :meth:`export_state`'s content changed: a breaker
        opened, reopened or closed (the store writes when it moved)."""
        return self._changes

    def export_state(self) -> list[dict[str, Any]]:
        """The breakers that are not closed, with the cooldown left.

        A half-open probe ends with the process, so its breaker has no
        cooldown left: after a restart the next call probes.
        """
        now = time.monotonic()
        entries: list[dict[str, Any]] = []
        for name, state in self._states.items():
            cooldown = self._cooldowns.get(name, self._cooldown)
            remaining = 0.0
            if state == _State.OPEN:
                elapsed = now - self._opened_at.get(name, 0.0)
                remaining = max(0.0, cooldown - elapsed)
            entries.append(
                {"name": name, "remaining_cooldown": remaining, "cooldown": cooldown}
            )
        return entries

    def import_state(self, entries: Iterable[dict[str, Any]], age_s: float) -> int:
        """Restore :meth:`export_state`'s breakers as open, with the cooldown
        left shortened by *age_s* (the time since the export); returns how
        many.

        A cooldown that ran out meanwhile leaves the probe due instead of
        closing the breaker: a failed probe still doubles the cooldown.
        Raises ``KeyError``, ``TypeError`` or ``ValueError`` for a malformed
        entry, before changing anything.
        """
        restored = []
        for entry in entries:
            cooldown = min(float(entry["cooldown"]), self._max_cooldown)
            remaining = float(entry["remaining_cooldown"]) - age_s
            restored.append((str(entry["name"]), cooldown, min(remaining, cooldown)))
        now = time.monotonic()
        for name, cooldown, remaining in restored:
            self._states[name] = _State.OPEN
            # Open means a failure streak of at least the threshold
            self._failures[name] = self._threshold
            self._cooldowns[name] = cooldown
            self._opened_at[name] = now - cooldown + max(0.0, remaining)
        return len(restored)
