"""Tests for the scrape-time collectors (breakers, container, browser pages)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from prometheus_client import CollectorRegistry

from scavengarr.infrastructure.browser.page_gate import PageGate
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.telemetry.collectors import (
    BreakerCollector,
    BrowserPagesCollector,
    ContainerCollector,
)


def _open(breaker: PluginCircuitBreaker, name: str) -> None:
    for _ in range(5):
        breaker.record_failure(name)


class TestBreakerCollector:
    def test_lists_breakers_that_are_not_closed(self) -> None:
        plugins = PluginCircuitBreaker(failure_threshold=5)
        hosters = PluginCircuitBreaker(failure_threshold=5, cooldown_seconds=0.0)
        _open(plugins, "kinoking:2000")
        plugins.record_failure("kinoger:2000")  # one failure: still closed
        _open(hosters, "filemoon")
        assert hosters.allow("filemoon")  # cooldown over: half-open probe
        registry = CollectorRegistry()
        registry.register(BreakerCollector({"plugin": plugins, "hoster": hosters}))

        def value(breaker: str, name: str, state: str) -> float | None:
            return registry.get_sample_value(
                "scavengarr_circuit_breaker_open",
                {"breaker": breaker, "name": name, "state": state},
            )

        assert value("plugin", "kinoking:2000", "open") == 1
        assert value("hoster", "filemoon", "half_open") == 1
        assert value("plugin", "kinoger:2000", "closed") is None

    def test_reads_the_state_at_scrape_time(self) -> None:
        breaker = PluginCircuitBreaker(failure_threshold=5)
        registry = CollectorRegistry()
        registry.register(BreakerCollector({"plugin": breaker}))
        _open(breaker, "kinoking:2000")
        labels = {"breaker": "plugin", "name": "kinoking:2000", "state": "open"}

        assert registry.get_sample_value("scavengarr_circuit_breaker_open", labels) == 1

        breaker.record_success("kinoking:2000")

        assert (
            registry.get_sample_value("scavengarr_circuit_breaker_open", labels) is None
        )


def _cgroup(tmp_path: Path, *, own: str = "/") -> tuple[Path, Path]:
    root = tmp_path / "cgroup"
    directory = root / own.lstrip("/")
    directory.mkdir(parents=True)
    (directory / "cpu.stat").write_text(
        "usage_usec 1004060663\nuser_usec 788980561\nsystem_usec 215080102\n"
    )
    (directory / "memory.current").write_text("666824704\n")
    proc = tmp_path / "proc_self_cgroup"
    proc.write_text(f"0::{own}\n")
    return root, proc


class TestContainerCollector:
    def _registry(self, root: Path, proc: Path) -> CollectorRegistry:
        registry = CollectorRegistry()
        registry.register(ContainerCollector(root=root, proc_cgroup=proc))
        return registry

    def test_cpu_and_memory_of_the_container(self, tmp_path: Path) -> None:
        registry = self._registry(*_cgroup(tmp_path))

        cpu = registry.get_sample_value("scavengarr_container_cpu_seconds_total")
        assert cpu == 1004.060663
        memory = registry.get_sample_value("scavengarr_container_memory_bytes")
        assert memory == 666824704

    def test_the_process_own_cgroup(self, tmp_path: Path) -> None:
        own = "/system.slice/docker-abc.scope"
        registry = self._registry(*_cgroup(tmp_path, own=own))

        assert registry.get_sample_value("scavengarr_container_memory_bytes")

    def test_nothing_without_cgroup_v2(self, tmp_path: Path) -> None:
        proc = tmp_path / "proc_self_cgroup"
        proc.write_text("12:memory:/docker/abc\n")  # cgroup v1 only
        registry = self._registry(tmp_path / "missing", proc)

        assert (
            registry.get_sample_value("scavengarr_container_cpu_seconds_total") is None
        )
        assert registry.get_sample_value("scavengarr_container_memory_bytes") is None

    def test_nothing_without_proc(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path, tmp_path / "missing")

        assert registry.get_sample_value("scavengarr_container_memory_bytes") is None


class TestBrowserPagesCollector:
    async def test_limit_pages_in_use_and_waiting(self) -> None:
        gate = PageGate(limit=1)
        registry = CollectorRegistry()
        registry.register(BrowserPagesCollector(gate))
        release = asyncio.Event()

        async def _hold() -> None:
            async with gate.page("plugin", timeout=30):
                await release.wait()

        tasks = [asyncio.create_task(_hold()) for _ in range(3)]
        for _ in range(5):
            await asyncio.sleep(0)

        def pages(state: str) -> float | None:
            return registry.get_sample_value(
                "scavengarr_browser_pages", {"state": state}
            )

        assert (pages("limit"), pages("in_use"), pages("waiting")) == (1, 1, 2)

        release.set()
        await asyncio.gather(*tasks)
        assert (pages("in_use"), pages("waiting")) == (0, 0)
