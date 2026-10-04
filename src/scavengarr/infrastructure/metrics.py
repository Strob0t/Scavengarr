"""Zero-impact in-memory performance metrics.

All counters are plain Python integers manipulated inside the single-threaded
async event loop — no locks, no I/O, no disk, no external dependencies.

``time.perf_counter_ns()`` is used for timing (monotonic, nanosecond
resolution, near-zero overhead).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)

# Event-loop lag: one timer every LAG_INTERVAL_S, the last LAG_WINDOW samples
# kept (5 min); a stall of LAG_WARN_MS or more is logged
LAG_INTERVAL_S = 0.5
LAG_WINDOW = 600
LAG_WARN_MS = 250.0


@dataclass
class PluginStats:
    """Accumulated statistics for a single plugin."""

    searches: int = 0
    successes: int = 0
    failures: int = 0
    total_results: int = 0
    total_duration_ns: int = 0

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable summary."""
        avg_ms = (
            round(self.total_duration_ns / self.searches / 1_000_000, 1)
            if self.searches
            else 0.0
        )
        return {
            "searches": self.searches,
            "successes": self.successes,
            "failures": self.failures,
            "total_results": self.total_results,
            "avg_duration_ms": avg_ms,
        }


@dataclass
class MetricsCollector:
    """Central in-memory metrics collector.

    Thread-safety is not required — the async event loop is
    single-threaded, so plain integer increments are atomic enough.
    """

    _plugins: dict[str, PluginStats] = field(default_factory=dict)
    _start_ns: int = field(default_factory=time.perf_counter_ns)
    _loop_lag_ms: deque[float] = field(default_factory=lambda: deque(maxlen=LAG_WINDOW))

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_plugin_search(
        self,
        name: str,
        duration_ns: int,
        result_count: int,
        *,
        success: bool,
    ) -> None:
        """Record one plugin search invocation."""
        stats = self._plugins.get(name)
        if stats is None:
            stats = PluginStats()
            self._plugins[name] = stats

        stats.searches += 1
        stats.total_duration_ns += duration_ns

        if success:
            stats.successes += 1
            stats.total_results += result_count
        else:
            stats.failures += 1

    def record_loop_lag(self, lag_ms: float) -> None:
        """Record how late one event-loop timer fired (``monitor_loop_lag``)."""
        self._loop_lag_ms.append(lag_ms)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot of all metrics."""
        uptime_ns = time.perf_counter_ns() - self._start_ns
        uptime_s = round(uptime_ns / 1_000_000_000, 1)

        return {
            "uptime_seconds": uptime_s,
            "plugins": {
                name: stats.snapshot() for name, stats in sorted(self._plugins.items())
            },
            "event_loop": self._loop_lag_snapshot(),
        }

    def _loop_lag_snapshot(self) -> dict[str, object]:
        """Percentiles of the event-loop lag over the last ``LAG_WINDOW`` samples."""
        samples = sorted(self._loop_lag_ms)
        if not samples:
            return {"samples": 0}

        def _pct(share: float) -> float:
            return round(samples[min(len(samples) - 1, int(share * len(samples)))], 1)

        return {
            "samples": len(samples),
            "lag_ms_p50": _pct(0.5),
            "lag_ms_p99": _pct(0.99),
            "lag_ms_max": round(samples[-1], 1),
        }


async def monitor_loop_lag(
    metrics: MetricsCollector,
    *,
    interval: float = LAG_INTERVAL_S,
    warn_ms: float = LAG_WARN_MS,
) -> None:
    """Record how late a periodic timer fires, until cancelled.

    A timer due every *interval* seconds fires late by as long as callbacks
    held the event loop (parsing, logging, TLS handshakes); every timeout and
    deadline of a request is late by the same amount. A stall of *warn_ms*
    or more is logged as ``event_loop_lag``.
    """
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()
        await asyncio.sleep(interval)
        lag_ms = max(0.0, (loop.time() - start - interval) * 1000)
        metrics.record_loop_lag(lag_ms)
        if lag_ms >= warn_ms:
            log.warning("event_loop_lag", lag_ms=round(lag_ms, 1))
