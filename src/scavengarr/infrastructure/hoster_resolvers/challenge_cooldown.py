"""The challenge cooldown: how long a hoster's links wait after a challenge.

A resolution that showed a challenge and found no stream in time (outcome
``challenge``) says that the hoster wants a warm browser context, not that it
is broken, so the circuit breaker does not count it. The hoster's links are
skipped for a while instead, longer after each further challenge
(``_STEPS_S``), and the hoster's next stream ends the cooldown and resets
its steps. Memory only: a restart starts the browser cold, and a kept
cooldown would only delay the capture that warms it. See
``openspec/changes/react-to-challenges/design.md``.
"""

from __future__ import annotations

import time

# The cooldown after the first challenge in a row, the second, ...; the
# last one repeats
_STEPS_S: tuple[float, ...] = (60.0, 300.0, 900.0, 1800.0)


def _now() -> float:
    return time.monotonic()


class ChallengeCooldown:
    """Per resolver name, the steps taken and when the cooldown ends."""

    def __init__(self) -> None:
        self._steps: dict[str, int] = {}
        self._ends: dict[str, float] = {}

    def left(self, name: str) -> float:
        """Seconds left of *name*'s cooldown, 0 when none runs."""
        return max(0.0, self._ends.get(name, 0.0) - _now())

    def challenged(self, name: str) -> float:
        """A resolution of *name* ended as ``challenge``: start the next
        step's cooldown. Returns the seconds it runs.

        A challenge while a cooldown runs comes from a resolution that
        started before it: it keeps the running cooldown, so a burst of
        concurrent captures counts as one step.
        """
        if left := self.left(name):
            return left
        step = self._steps.get(name, 0)
        seconds = _STEPS_S[min(step, len(_STEPS_S) - 1)]
        self._steps[name] = step + 1
        self._ends[name] = _now() + seconds
        return seconds

    def streamed(self, name: str) -> None:
        """A stream from *name*: no cooldown, and the next challenge starts
        at the first step again."""
        self._steps.pop(name, None)
        self._ends.pop(name, None)
