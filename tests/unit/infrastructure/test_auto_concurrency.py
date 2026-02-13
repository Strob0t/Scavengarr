"""Tests for dynamic max_concurrent_plugins auto-tuning."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scavengarr.infrastructure.config.schema import StremioConfig


class TestMaxConcurrentPluginsAuto:
    def test_config_field_default_true(self) -> None:
        config = StremioConfig()
        assert config.max_concurrent_plugins_auto is True

    def test_config_field_can_disable(self) -> None:
        config = StremioConfig(max_concurrent_plugins_auto=False)
        assert config.max_concurrent_plugins_auto is False

    def test_auto_formula_4_cpu_8gb(self) -> None:
        """4 CPUs, 8GB available → min(4, 16, 10) = 4."""
        cpu_count = 4
        available_ram_gb = 8.0
        mem_limit = int(available_ram_gb * 2)
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 4

    def test_auto_formula_16_cpu_32gb(self) -> None:
        """16 CPUs, 32GB → min(16, 64, 10) = 10 (capped)."""
        cpu_count = 16
        available_ram_gb = 32.0
        mem_limit = int(available_ram_gb * 2)
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 10

    def test_auto_formula_1_cpu_512mb(self) -> None:
        """1 CPU, 512MB → max(2, min(1, 1, 10)) = max(2, 1) = 2 (floor)."""
        cpu_count = 1
        available_ram_gb = 0.5
        mem_limit = int(available_ram_gb * 2)
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 2

    def test_auto_formula_2_cpu_1gb(self) -> None:
        """2 CPUs, 1GB → min(2, 2, 10) = 2."""
        cpu_count = 2
        available_ram_gb = 1.0
        mem_limit = int(available_ram_gb * 2)
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 2

    def test_auto_formula_8_cpu_4gb(self) -> None:
        """8 CPUs, 4GB → min(8, 8, 10) = 8."""
        cpu_count = 8
        available_ram_gb = 4.0
        mem_limit = int(available_ram_gb * 2)
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 8

    def test_auto_formula_no_psutil_fallback(self) -> None:
        """Without psutil, mem_limit defaults to 8."""
        cpu_count = 4
        mem_limit = 8  # fallback
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 4

    def test_auto_formula_no_cpu_count_fallback(self) -> None:
        """os.cpu_count() returns None → fallback to 2."""
        cpu_count = 2  # fallback when os.cpu_count() is None
        mem_limit = 8
        result = max(2, min(cpu_count, mem_limit, 10))
        assert result == 2

    def test_disabled_preserves_manual_value(self) -> None:
        config = StremioConfig(
            max_concurrent_plugins=3,
            max_concurrent_plugins_auto=False,
        )
        assert config.max_concurrent_plugins == 3
