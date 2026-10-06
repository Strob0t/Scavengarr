"""Metrics read when Prometheus scrapes: no cost between scrapes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from prometheus_client.metrics_core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    Metric,
)
from prometheus_client.registry import Collector

from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.resource_detector import (
    own_cgroup,
    read_cgroup_value,
)


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


class BrowserPagesCollector(Collector):
    """The stealth browser's page limit, pages in use and waiting requests."""

    def __init__(self, gate: PageGate) -> None:
        self._gate = gate

    def collect(self) -> Iterator[Metric]:
        gauge = GaugeMetricFamily(
            "scavengarr_browser_pages",
            "Stealth browser pages: the limit, in use, requests waiting",
            labels=("state",),
        )
        gauge.add_metric(("limit",), self._gate.limit)
        gauge.add_metric(("in_use",), self._gate.in_use)
        gauge.add_metric(("waiting",), self._gate.waiting)
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
        self._dir = own_cgroup(root, proc_cgroup)

    def collect(self) -> Iterator[Metric]:
        if self._dir is None:
            return
        usage_usec = read_cgroup_value(self._dir / "cpu.stat", "usage_usec")
        if usage_usec is not None:
            yield CounterMetricFamily(
                "scavengarr_container_cpu_seconds",
                "CPU time of the container (cgroup v2), Chromium included",
                value=usage_usec / 1_000_000,
            )
        memory = read_cgroup_value(self._dir / "memory.current")
        if memory is not None:
            yield GaugeMetricFamily(
                "scavengarr_container_memory_bytes",
                "Memory of the container (cgroup v2), page cache included",
                value=memory,
            )
