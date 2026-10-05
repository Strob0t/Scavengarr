"""Telemetry port: timed steps (stages) and values, for metrics and traces.

A stage is one step of a request (a plugin search, a hoster resolution, a
phase of a Stremio request). It is recorded when it ends: its duration and
its outcome, and with tracing on it is a span. Label values come from small
fixed sets only (plugin and resolver names, outcomes), never titles, ids,
URLs or domains.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Literal, Protocol, runtime_checkable

StageName = Literal[
    "stremio_request",
    "stremio_phase",
    "plugin_search",
    "hoster_resolve",
    "hls_proxy",
]

ValueName = Literal[
    "stremio_streams",
    "plugin_results",
    "hls_proxy_bytes",
]

AttributeValue = str | int | float | bool


class Stage(Protocol):
    """A running stage; its outcome and labels are recorded when it ends.

    Without an outcome set, the stage ends as ``ok``, ``cut`` (cancelled),
    ``timeout`` (``TimeoutError``) or ``error`` (any other exception).
    """

    outcome: str | None

    def label(self, **labels: str) -> None:
        """Set labels known only while the stage runs."""
        ...

    def annotate(self, **attributes: AttributeValue) -> None:
        """Add details to the stage's span (traces only, not metrics)."""
        ...


@runtime_checkable
class TelemetryPort(Protocol):
    """Records stages, outcomes without a run, and values."""

    def stage(self, name: StageName, /, **labels: str) -> AbstractContextManager[Stage]:
        """Time the ``with`` block as stage *name*; exceptions propagate."""
        ...

    def count(self, name: StageName, outcome: str, /, **labels: str) -> None:
        """Count an outcome of stage *name* that did not run (no duration)."""
        ...

    def record(self, name: ValueName, value: float, /, **labels: str) -> None:
        """Record a value that is not a duration (a count, bytes)."""
        ...


class _NoStage:
    def __init__(self) -> None:
        self.outcome: str | None = None

    def label(self, **labels: str) -> None:
        pass

    def annotate(self, **attributes: AttributeValue) -> None:
        pass


class _NoTelemetry:
    """Records nothing: the default where no telemetry is wired in."""

    def stage(self, name: StageName, /, **labels: str) -> AbstractContextManager[Stage]:
        return nullcontext(_NoStage())

    def count(self, name: StageName, outcome: str, /, **labels: str) -> None:
        pass

    def record(self, name: ValueName, value: float, /, **labels: str) -> None:
        pass


NO_TELEMETRY: TelemetryPort = _NoTelemetry()
