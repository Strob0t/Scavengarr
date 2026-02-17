"""Zero-impact in-memory performance metrics.

All counters are plain Python integers manipulated inside the single-threaded
async event loop — no locks, no I/O, no disk, no external dependencies.

``time.perf_counter_ns()`` is used for timing (monotonic, nanosecond
resolution, near-zero overhead).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


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
class ProbeStats:
    """Accumulated statistics for stealth probe runs."""

    runs: int = 0
    total_urls: int = 0
    alive: int = 0
    dead: int = 0
    cf_blocked: int = 0
    total_duration_ns: int = 0

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable summary."""
        avg_ms = (
            round(self.total_duration_ns / self.runs / 1_000_000, 1)
            if self.runs
            else 0.0
        )
        return {
            "runs": self.runs,
            "total_urls": self.total_urls,
            "alive": self.alive,
            "dead": self.dead,
            "cf_blocked": self.cf_blocked,
            "avg_duration_ms": avg_ms,
        }


@dataclass
class MetricsCollector:
    """Central in-memory metrics collector.

    Thread-safety is not required — the async event loop is
    single-threaded, so plain integer increments are atomic enough.
    """

    _plugins: dict[str, PluginStats] = field(default_factory=dict)
    _probe: ProbeStats = field(default_factory=ProbeStats)
    _start_ns: int = field(default_factory=time.perf_counter_ns)

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

    def record_probe(
        self,
        total: int,
        alive: int,
        dead: int,
        cf_blocked: int,
        duration_ns: int,
    ) -> None:
        """Record one probe run."""
        self._probe.runs += 1
        self._probe.total_urls += total
        self._probe.alive += alive
        self._probe.dead += dead
        self._probe.cf_blocked += cf_blocked
        self._probe.total_duration_ns += duration_ns

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
            "probe": self._probe.snapshot(),
        }
