"""Telemetry: Prometheus metrics of the core's stages and the event-loop monitor."""

from __future__ import annotations

from scavengarr.infrastructure.telemetry.loop_lag import monitor_loop_lag
from scavengarr.infrastructure.telemetry.metrics import (
    CONTENT_TYPE,
    LAG_WINDOW,
    Telemetry,
)

__all__ = ["CONTENT_TYPE", "LAG_WINDOW", "Telemetry", "monitor_loop_lag"]
