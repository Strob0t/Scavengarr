"""Tests for container-aware resource detection via cgroup v2/v1."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scavengarr.infrastructure.resource_detector import (
    ResourceSampler,
    _detect_cpu_v1,
    _detect_cpu_v2,
    _detect_mem_v1,
    _detect_mem_v2,
    _fallback_cpu,
    _fallback_mem,
    detect_resources,
)

_MOD = "scavengarr.infrastructure.resource_detector"


def _mock_read_file(mapping: dict[Path, str | None]):
    """Patch ``_read_file`` with a dict mapping Path → content (None = missing)."""

    def _side_effect(path: Path) -> str | None:
        return mapping.get(path)

    return patch(f"{_MOD}._read_file", side_effect=_side_effect)


class TestDetectCpuV2:
    """cgroup v2 CPU detection from /sys/fs/cgroup/cpu.max."""

    def test_2_cores(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "200000 100000"}):
            assert _detect_cpu_v2() == 2

    def test_4_cores(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "400000 100000"}):
            assert _detect_cpu_v2() == 4

    def test_fractional_half_rounds_up(self) -> None:
        """0.5 CPU → ceil(50000/100000) = 1."""
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "50000 100000"}):
            assert _detect_cpu_v2() == 1

    def test_fractional_1_5_rounds_up(self) -> None:
        """1.5 CPUs → ceil(150000/100000) = 2."""
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "150000 100000"}):
            assert _detect_cpu_v2() == 2

    def test_unlimited_returns_none(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "max 100000"}):
            assert _detect_cpu_v2() is None

    def test_missing_file_returns_none(self) -> None:
        with _mock_read_file({}):
            assert _detect_cpu_v2() is None

    def test_invalid_format_returns_none(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "garbage"}):
            assert _detect_cpu_v2() is None

    def test_zero_quota_returns_none(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "0 100000"}):
            assert _detect_cpu_v2() is None

    def test_zero_period_returns_none(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_CPU

        with _mock_read_file({_CGROUP_V2_CPU: "200000 0"}):
            assert _detect_cpu_v2() is None


class TestDetectCpuV1:
    """cgroup v1 CPU detection from cfs_quota_us / cfs_period_us."""

    def test_2_cores(self) -> None:
        from scavengarr.infrastructure.resource_detector import (
            _CGROUP_V1_CPU_PERIOD,
            _CGROUP_V1_CPU_QUOTA,
        )

        with _mock_read_file(
            {
                _CGROUP_V1_CPU_QUOTA: "200000",
                _CGROUP_V1_CPU_PERIOD: "100000",
            }
        ):
            assert _detect_cpu_v1() == 2

    def test_unlimited_quota_minus_1(self) -> None:
        from scavengarr.infrastructure.resource_detector import (
            _CGROUP_V1_CPU_PERIOD,
            _CGROUP_V1_CPU_QUOTA,
        )

        with _mock_read_file(
            {
                _CGROUP_V1_CPU_QUOTA: "-1",
                _CGROUP_V1_CPU_PERIOD: "100000",
            }
        ):
            assert _detect_cpu_v1() is None

    def test_missing_quota_file(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V1_CPU_PERIOD

        with _mock_read_file({_CGROUP_V1_CPU_PERIOD: "100000"}):
            assert _detect_cpu_v1() is None

    def test_missing_period_file(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V1_CPU_QUOTA

        with _mock_read_file({_CGROUP_V1_CPU_QUOTA: "200000"}):
            assert _detect_cpu_v1() is None

    def test_fractional_rounds_up(self) -> None:
        """0.5 CPU → ceil(50000/100000) = 1."""
        from scavengarr.infrastructure.resource_detector import (
            _CGROUP_V1_CPU_PERIOD,
            _CGROUP_V1_CPU_QUOTA,
        )

        with _mock_read_file(
            {
                _CGROUP_V1_CPU_QUOTA: "50000",
                _CGROUP_V1_CPU_PERIOD: "100000",
            }
        ):
            assert _detect_cpu_v1() == 1


class TestDetectMemV2:
    """cgroup v2 memory detection from /sys/fs/cgroup/memory.max."""

    def test_2gb_limit(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_MEM

        limit = 2 * 1024**3
        with _mock_read_file({_CGROUP_V2_MEM: str(limit)}):
            assert _detect_mem_v2() == limit

    def test_unlimited_max(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_MEM

        with _mock_read_file({_CGROUP_V2_MEM: "max"}):
            assert _detect_mem_v2() is None

    def test_very_high_value_treated_as_unlimited(self) -> None:
        """Values >1TB are host values leaked into container."""
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_MEM

        limit = 2 * 1024**4  # 2 TB
        with _mock_read_file({_CGROUP_V2_MEM: str(limit)}):
            assert _detect_mem_v2() is None

    def test_missing_file(self) -> None:
        with _mock_read_file({}):
            assert _detect_mem_v2() is None

    def test_zero_value(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V2_MEM

        with _mock_read_file({_CGROUP_V2_MEM: "0"}):
            assert _detect_mem_v2() is None


class TestDetectMemV1:
    """cgroup v1 memory detection from memory.limit_in_bytes."""

    def test_4gb_limit(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V1_MEM

        limit = 4 * 1024**3
        with _mock_read_file({_CGROUP_V1_MEM: str(limit)}):
            assert _detect_mem_v1() == limit

    def test_very_high_value_treated_as_unlimited(self) -> None:
        from scavengarr.infrastructure.resource_detector import _CGROUP_V1_MEM

        limit = 2 * 1024**4
        with _mock_read_file({_CGROUP_V1_MEM: str(limit)}):
            assert _detect_mem_v1() is None

    def test_missing_file(self) -> None:
        with _mock_read_file({}):
            assert _detect_mem_v1() is None


class TestFallbacks:
    """OS-level fallback detection."""

    def test_fallback_cpu_uses_os_cpu_count(self) -> None:
        with patch("os.cpu_count", return_value=8):
            assert _fallback_cpu() == 8

    def test_fallback_cpu_none_returns_2(self) -> None:
        with patch("os.cpu_count", return_value=None):
            assert _fallback_cpu() == 2

    def test_fallback_mem_without_psutil_returns_default(self) -> None:
        """Without psutil, returns 4GB default."""
        import sys

        saved = sys.modules.get("psutil")
        sys.modules["psutil"] = None  # type: ignore[assignment]
        try:
            result = _fallback_mem()
            assert result == 4 * 1024**3
        finally:
            if saved is not None:
                sys.modules["psutil"] = saved
            else:
                sys.modules.pop("psutil", None)


class TestDetectResources:
    """Integration: detect_resources() cascading detection."""

    def test_cgroup_v2_detected(self) -> None:
        with (
            patch(f"{_MOD}._detect_cpu_v2", return_value=2),
            patch(f"{_MOD}._detect_mem_v2", return_value=2 * 1024**3),
        ):
            result = detect_resources()
            assert result.cpu_cores == 2
            assert result.memory_bytes == 2 * 1024**3
            assert result.cpu_source == "cgroup_v2"
            assert result.mem_source == "cgroup_v2"
            assert result.cgroup_limited is True

    def test_cgroup_v1_fallback(self) -> None:
        with (
            patch(f"{_MOD}._detect_cpu_v2", return_value=None),
            patch(f"{_MOD}._detect_mem_v2", return_value=None),
            patch(f"{_MOD}._detect_cpu_v1", return_value=4),
            patch(f"{_MOD}._detect_mem_v1", return_value=4 * 1024**3),
        ):
            result = detect_resources()
            assert result.cpu_cores == 4
            assert result.memory_bytes == 4 * 1024**3
            assert result.cpu_source == "cgroup_v1"
            assert result.mem_source == "cgroup_v1"
            assert result.cgroup_limited is True

    def test_os_fallback(self) -> None:
        with (
            patch(f"{_MOD}._detect_cpu_v2", return_value=None),
            patch(f"{_MOD}._detect_mem_v2", return_value=None),
            patch(f"{_MOD}._detect_cpu_v1", return_value=None),
            patch(f"{_MOD}._detect_mem_v1", return_value=None),
            patch(f"{_MOD}._fallback_cpu", return_value=8),
            patch(f"{_MOD}._fallback_mem", return_value=16 * 1024**3),
        ):
            result = detect_resources()
            assert result.cpu_cores == 8
            assert result.memory_bytes == 16 * 1024**3
            assert result.cpu_source == "os_fallback"
            assert result.mem_source == "os_fallback"
            assert result.cgroup_limited is False

    def test_mixed_sources(self) -> None:
        """CPU from cgroup v2, memory falls back to OS."""
        with (
            patch(f"{_MOD}._detect_cpu_v2", return_value=2),
            patch(f"{_MOD}._detect_mem_v2", return_value=None),
            patch(f"{_MOD}._detect_mem_v1", return_value=None),
            patch(f"{_MOD}._fallback_mem", return_value=8 * 1024**3),
        ):
            result = detect_resources()
            assert result.cpu_source == "cgroup_v2"
            assert result.mem_source == "os_fallback"
            assert result.cgroup_limited is True

    def test_result_is_frozen(self) -> None:
        with (
            patch(f"{_MOD}._detect_cpu_v2", return_value=2),
            patch(f"{_MOD}._detect_mem_v2", return_value=2 * 1024**3),
        ):
            result = detect_resources()
            with pytest.raises(AttributeError):
                result.cpu_cores = 99  # type: ignore[misc]


class _Machine:
    """Fake ``/proc`` and cgroup v2 files of a container."""

    def __init__(self, tmp_path: Path) -> None:
        self.proc = tmp_path / "proc"
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "self" / "cgroup").write_text("0::/\n")
        self.cgroup = tmp_path / "cgroup"
        self.cgroup.mkdir()
        self.cpu(busy=0, idle=0)
        self.host_memory(available_mb=4000)
        self.container(usage_s=0.0, quota=None)
        self.memory(limit_mb=None, current_mb=500, inactive_mb=100)

    def cpu(self, *, busy: int, idle: int) -> None:
        """Host jiffies: *busy* split over user/system/softirq, *idle* + iowait."""
        user, system, softirq = busy // 2, busy // 4, busy - busy // 2 - busy // 4
        (self.proc / "stat").write_text(
            f"cpu  {user} 0 {system} {idle} 0 0 {softirq} 0 0 0\n"
            "cpu0 1 0 1 1 0 0 0 0 0 0\n"
        )

    def host_memory(self, *, available_mb: int) -> None:
        (self.proc / "meminfo").write_text(
            f"MemTotal:        8000000 kB\nMemAvailable:    {available_mb * 1024} kB\n"
        )

    def container(self, *, usage_s: float, quota: float | None) -> None:
        (self.cgroup / "cpu.stat").write_text(
            f"usage_usec {int(usage_s * 1_000_000)}\nuser_usec 0\n"
        )
        limit = "max" if quota is None else str(int(quota * 100_000))
        (self.cgroup / "cpu.max").write_text(f"{limit} 100000\n")

    def memory(
        self, *, limit_mb: int | None, current_mb: int, inactive_mb: int
    ) -> None:
        mb = 1024**2
        limit = "max" if limit_mb is None else str(limit_mb * mb)
        (self.cgroup / "memory.max").write_text(f"{limit}\n")
        (self.cgroup / "memory.current").write_text(f"{current_mb * mb}\n")
        (self.cgroup / "memory.stat").write_text(
            f"anon 1\ninactive_file {inactive_mb * mb}\nactive_file 2\n"
        )

    def sampler(self) -> ResourceSampler:
        return ResourceSampler(proc=self.proc, cgroup_root=self.cgroup)


class TestResourceSampler:
    """What the browser page budget reads every few seconds."""

    def test_the_first_sample_has_no_cpu_share(self, tmp_path: Path) -> None:
        machine = _Machine(tmp_path)

        assert machine.sampler().sample().cpu_busy is None

    def test_the_hosts_busy_share_since_the_last_sample(self, tmp_path: Path) -> None:
        machine = _Machine(tmp_path)
        sampler = machine.sampler()
        machine.cpu(busy=1000, idle=1000)
        sampler.sample()

        machine.cpu(busy=1300, idle=1100)  # 300 of 400 jiffies busy

        assert sampler.sample().cpu_busy == pytest.approx(0.75)

    def test_a_cpu_limit_counts_when_the_container_is_busier(
        self, tmp_path: Path
    ) -> None:
        """Under ``--cpus 1`` the container can be at its limit on an idle host."""
        machine = _Machine(tmp_path)
        sampler = machine.sampler()
        machine.container(usage_s=10.0, quota=1.0)
        with patch(f"{_MOD}.time.monotonic", return_value=100.0):
            sampler.sample()

        machine.container(usage_s=10.95, quota=1.0)  # 0.95 s in 1 s
        machine.cpu(busy=10, idle=90)
        with patch(f"{_MOD}.time.monotonic", return_value=101.0):
            usage = sampler.sample()

        assert usage.cpu_busy == pytest.approx(0.95)

    def test_free_memory_is_the_hosts_without_a_limit(self, tmp_path: Path) -> None:
        machine = _Machine(tmp_path)
        machine.host_memory(available_mb=3000)

        assert machine.sampler().sample().memory_free == 3000 * 1024**2

    def test_a_memory_limit_counts_when_it_leaves_less(self, tmp_path: Path) -> None:
        """The page cache (inactive files) is given back under pressure."""
        machine = _Machine(tmp_path)
        machine.host_memory(available_mb=3000)
        machine.memory(limit_mb=2000, current_mb=1800, inactive_mb=300)

        assert machine.sampler().sample().memory_free == 500 * 1024**2

    def test_nothing_known_without_the_files(self, tmp_path: Path) -> None:
        sampler = ResourceSampler(
            proc=tmp_path / "missing", cgroup_root=tmp_path / "missing"
        )
        sampler.sample()

        usage = sampler.sample()

        assert (usage.cpu_busy, usage.memory_free) == (None, None)
