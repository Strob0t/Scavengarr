"""Tests for container-aware auto-tuning of concurrency parameters."""

from __future__ import annotations

from unittest.mock import patch

from scavengarr.infrastructure.config.schema import AppConfig, StremioConfig
from scavengarr.infrastructure.resource_detector import DetectedResources
from scavengarr.interfaces.composition import _auto_tune, _auto_tune_concurrency


class TestMaxConcurrentPluginsAuto:
    """Legacy single-parameter auto-tune tests."""

    def test_config_field_default_true(self) -> None:
        config = StremioConfig()
        assert config.max_concurrent_plugins_auto is True

    def test_config_field_can_disable(self) -> None:
        config = StremioConfig(max_concurrent_plugins_auto=False)
        assert config.max_concurrent_plugins_auto is False

    def test_disabled_preserves_manual_value(self) -> None:
        config = StremioConfig(
            max_concurrent_plugins=3,
            max_concurrent_plugins_auto=False,
        )
        assert config.max_concurrent_plugins == 3


class TestAutoTuneAll:
    """Container-aware auto-tuning of ALL concurrency parameters."""

    def test_config_field_default_true(self) -> None:
        config = StremioConfig()
        assert config.auto_tune_all is True

    def test_config_field_can_disable(self) -> None:
        config = StremioConfig(auto_tune_all=False)
        assert config.auto_tune_all is False

    def test_small_container_1cpu_512mb(self) -> None:
        """1 CPU, 512MB → minimal concurrency."""
        resources = DetectedResources(
            cpu_cores=1,
            memory_bytes=512 * 1024**2,
            cpu_source="cgroup_v2",
            mem_source="cgroup_v2",
            cgroup_limited=True,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        assert config.stremio.max_concurrent_plugins == 2  # max(2, min(3, 1, 30))
        assert config.stremio.max_concurrent_playwright == 1  # max(1, min(1, 3, 10))
        assert config.stremio.probe_concurrency == 4  # max(4, 4)
        assert config.validation_max_concurrent == 5  # max(5, 5)

    def test_medium_container_2cpu_2gb(self) -> None:
        """2 CPUs, 2GB → moderate concurrency."""
        resources = DetectedResources(
            cpu_cores=2,
            memory_bytes=2 * 1024**3,
            cpu_source="cgroup_v2",
            mem_source="cgroup_v2",
            cgroup_limited=True,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        assert config.stremio.max_concurrent_plugins == 4  # min(6, 4, 30)
        assert config.stremio.max_concurrent_playwright == 2  # min(2, 13, 10)
        assert config.stremio.probe_concurrency == 8  # 2*4
        assert config.validation_max_concurrent == 10  # 2*5

    def test_large_container_4cpu_8gb(self) -> None:
        """4 CPUs, 8GB → higher concurrency."""
        resources = DetectedResources(
            cpu_cores=4,
            memory_bytes=8 * 1024**3,
            cpu_source="cgroup_v1",
            mem_source="cgroup_v1",
            cgroup_limited=True,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        assert config.stremio.max_concurrent_plugins == 12  # min(12, 16, 30)
        assert config.stremio.max_concurrent_playwright == 4  # min(4, 53, 10)
        assert config.stremio.probe_concurrency == 16  # 4*4
        assert config.validation_max_concurrent == 20  # 4*5

    def test_large_host_8cpu_16gb(self) -> None:
        """8 CPUs, 16GB → high concurrency, capped."""
        resources = DetectedResources(
            cpu_cores=8,
            memory_bytes=16 * 1024**3,
            cpu_source="os_fallback",
            mem_source="os_fallback",
            cgroup_limited=False,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        assert config.stremio.max_concurrent_plugins == 24  # min(24, 32, 30)
        assert config.stremio.max_concurrent_playwright == 8  # min(8, 106, 10)
        assert config.stremio.probe_concurrency == 32  # 8*4
        assert config.validation_max_concurrent == 40  # 8*5

    def test_very_large_host_capped_at_30(self) -> None:
        """16 CPUs, 64GB → plugins capped at 30."""
        resources = DetectedResources(
            cpu_cores=16,
            memory_bytes=64 * 1024**3,
            cpu_source="os_fallback",
            mem_source="os_fallback",
            cgroup_limited=False,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        assert config.stremio.max_concurrent_plugins == 30  # min(48, 128, 30)
        assert (
            config.stremio.max_concurrent_playwright == 10
        )  # min(16, 426, 10) → capped

    def test_playwright_limited_by_memory(self) -> None:
        """4 CPUs but only 256MB → Playwright limited by RAM."""
        resources = DetectedResources(
            cpu_cores=4,
            memory_bytes=256 * 1024**2,
            cpu_source="cgroup_v2",
            mem_source="cgroup_v2",
            cgroup_limited=True,
        )
        config = AppConfig()
        with patch(
            "scavengarr.interfaces.composition.detect_resources",
            return_value=resources,
        ):
            _auto_tune(config)

        # 256MB = 0.25GB → max(1, min(4, int(0.25/0.15), 10)) = 1
        assert config.stremio.max_concurrent_playwright == 1


class TestLegacyAutoTuneConcurrency:
    """Ensure the legacy _auto_tune_concurrency still works."""

    def test_respects_disabled_flag(self) -> None:
        config = AppConfig()
        config.stremio.max_concurrent_plugins_auto = False
        config.stremio.max_concurrent_plugins = 3
        _auto_tune_concurrency(config)
        assert config.stremio.max_concurrent_plugins == 3

    def test_auto_tune_basic(self) -> None:
        config = AppConfig()
        config.stremio.max_concurrent_plugins_auto = True
        mock_vmem = type("VirtualMemory", (), {"available": 8 * 1024**3})()
        mock_psutil = type("MockPsutil", (), {"virtual_memory": lambda: mock_vmem})
        with (
            patch("os.cpu_count", return_value=4),
            patch.dict("sys.modules", {"psutil": mock_psutil}),
        ):
            _auto_tune_concurrency(config)

        assert config.stremio.max_concurrent_plugins == 4  # min(4, 16, 20)
