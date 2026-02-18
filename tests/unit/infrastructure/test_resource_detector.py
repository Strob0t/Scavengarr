"""Tests for container-aware resource detection via cgroup v2/v1."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scavengarr.infrastructure.resource_detector import (
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
