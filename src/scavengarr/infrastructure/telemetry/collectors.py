"""Metrics read when Prometheus scrapes: no cost between scrapes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import structlog
from prometheus_client.metrics_core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    Metric,
)
from prometheus_client.registry import Collector

from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker

log = structlog.get_logger(__name__)


class BreakerCollector(Collector):
    """The circuit breakers that are not closed, by kind (``plugin``, ``hoster``)."""

    def __init__(self, breakers: Mapping[str, PluginCircuitBreaker]) -> None:
        self._breakers = breakers

    def collect(self) -> Iterator[Metric]:
        gauge = GaugeMetricFamily(
            "scavengarr_circuit_breaker_open",
            "Circuit breakers that are open or half-open (1)",
            labels=("breaker", "name", "state"),
        )
        for kind, breaker in self._breakers.items():
            for name, entry in breaker.snapshot().items():
                state = str(entry["state"])
                if state != "closed":
                    gauge.add_metric((kind, name, state), 1)
        yield gauge


class ContainerCollector(Collector):
    """CPU and memory of the process's cgroup (v2): the whole container.

    The container's CPU minus ``process_cpu_seconds_total`` is Chromium, its
    driver, Xvfb and the health checks. Without cgroup v2 nothing is
    collected.
    """

    def __init__(
        self,
        *,
        root: Path = Path("/sys/fs/cgroup"),
        proc_cgroup: Path = Path("/proc/self/cgroup"),
    ) -> None:
        self._dir = _own_cgroup(root, proc_cgroup)

    def collect(self) -> Iterator[Metric]:
        if self._dir is None:
            return
        usage_usec = _read_value(self._dir / "cpu.stat", "usage_usec")
        if usage_usec is not None:
            yield CounterMetricFamily(
                "scavengarr_container_cpu_seconds",
                "CPU time of the container (cgroup v2), Chromium included",
                value=usage_usec / 1_000_000,
            )
        memory = _read_value(self._dir / "memory.current")
        if memory is not None:
            yield GaugeMetricFamily(
                "scavengarr_container_memory_bytes",
                "Memory of the container (cgroup v2), page cache included",
                value=memory,
            )


def _own_cgroup(root: Path, proc_cgroup: Path) -> Path | None:
    """The cgroup v2 directory of this process (``0::<path>``), if any."""
    try:
        lines = proc_cgroup.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            directory = root / line[3:].strip().lstrip("/")
            return directory if (directory / "cpu.stat").is_file() else None
    return None


def _read_value(path: Path, key: str | None = None) -> int | None:
    """The number in *path*, or the one after *key* (``cpu.stat``)."""
    try:
        text = path.read_text()
    except OSError as exc:
        log.debug("cgroup_read_failed", path=str(path), error=str(exc))
        return None
    if key is None:
        return int(text.strip())
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == key:
            return int(value)
    return None
