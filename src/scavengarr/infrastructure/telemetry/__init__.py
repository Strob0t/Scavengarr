"""Telemetry: Prometheus metrics of the core's stages, on-demand tracing and
the event-loop monitor."""

from __future__ import annotations

from scavengarr.infrastructure.telemetry.loop_lag import monitor_loop_lag
from scavengarr.infrastructure.telemetry.metrics import (
    CONTENT_TYPE,
    LAG_WINDOW,
    Telemetry,
)

__all__ = [
    "CONTENT_TYPE",
    "LAG_WINDOW",
    "Telemetry",
    "create_telemetry",
    "monitor_loop_lag",
]


def create_telemetry(tracing_endpoint: str | None) -> Telemetry:
    """Telemetry; with *tracing_endpoint* (OTLP/HTTP) also traces.

    OpenTelemetry is imported only for tracing: without it, it costs no
    memory and no startup time.
    """
    if not tracing_endpoint:
        return Telemetry()
    from scavengarr.infrastructure.telemetry.tracing import otlp_tracing

    return Telemetry(tracing=otlp_tracing(tracing_endpoint))
