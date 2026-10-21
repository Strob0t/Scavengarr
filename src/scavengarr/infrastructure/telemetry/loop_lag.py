"""Event-loop lag monitor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import structlog

from scavengarr.infrastructure.telemetry.metrics import Telemetry

log = structlog.get_logger(__name__)

# One timer every LAG_INTERVAL_S; a stall of LAG_WARN_MS or more is logged
LAG_INTERVAL_S = 0.5
LAG_WARN_MS = 250.0


async def monitor_loop_lag(
    telemetry: Telemetry,
    *,
    interval: float = LAG_INTERVAL_S,
    warn_ms: float = LAG_WARN_MS,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    """Record how late a periodic timer fires, until cancelled.

    A timer due every *interval* seconds fires late by as long as callbacks
    held the event loop (parsing, logging, TLS handshakes); every timeout and
    deadline of a request is late by the same amount. A stall of *warn_ms*
    or more is logged as ``event_loop_lag``. Every tick calls *heartbeat*:
    the stall monitor's sign of the loop's life (``StallMonitor.beat``).
    """
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()
        await asyncio.sleep(interval)
        if heartbeat is not None:
            heartbeat()
        lag_ms = max(0.0, (loop.time() - start - interval) * 1000)
        telemetry.record_loop_lag(lag_ms)
        if lag_ms >= warn_ms:
            log.warning("event_loop_lag", lag_ms=round(lag_ms, 1))
