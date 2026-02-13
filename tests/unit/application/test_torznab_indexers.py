"""Tests for TorznabIndexersUseCase."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from scavengarr.application.use_cases.torznab_indexers import (
    TorznabIndexersUseCase,
)


@dataclass
class _FakePlugin:
    name: str = "filmpalast"
    version: str = "1.0.0"
    mode: str = "httpx"


class TestTorznabIndexersUseCase:
    def test_returns_list_of_plugin_info(
        self,
        mock_plugin_registry: MagicMock,
    ) -> None:
        uc = TorznabIndexersUseCase(plugins=mock_plugin_registry)
        result = uc.execute()
        assert len(result) == 1
        assert result[0]["name"] == "filmpalast"

    def test_empty_registry(self) -> None:
        registry = MagicMock()
        registry.list_names.return_value = []
        uc = TorznabIndexersUseCase(plugins=registry)
        result = uc.execute()
        assert result == []

    def test_resilient_to_broken_plugin(self) -> None:
        registry = MagicMock()
        registry.list_names.return_value = ["broken"]
        registry.get.side_effect = RuntimeError("load error")
        uc = TorznabIndexersUseCase(plugins=registry)
        result = uc.execute()
        assert len(result) == 1
        assert result[0]["name"] == "broken"
        assert result[0]["version"] is None
        assert result[0]["mode"] is None

    def test_python_plugin_with_direct_mode(self) -> None:
        """Python plugins expose mode directly, not via scraping."""
        plugin = MagicMock()
        plugin.name = "boerse"
        plugin.version = "1.0.0"
        plugin.mode = "playwright"
        plugin.scraping = None

        registry = MagicMock()
        registry.list_names.return_value = ["boerse"]
        registry.get.return_value = plugin

        uc = TorznabIndexersUseCase(plugins=registry)
        result = uc.execute()

        assert len(result) == 1
        assert result[0]["name"] == "boerse"
        assert result[0]["version"] == "1.0.0"
        assert result[0]["mode"] == "playwright"

    def test_multiple_plugins(self) -> None:
        plugin_a = _FakePlugin(name="pluginA", version="1.0")
        plugin_b = _FakePlugin(name="pluginB", version="2.0")

        registry = MagicMock()
        registry.list_names.return_value = ["pluginA", "pluginB"]
        registry.get.side_effect = lambda n: plugin_a if n == "pluginA" else plugin_b

        uc = TorznabIndexersUseCase(plugins=registry)
        result = uc.execute()
        assert len(result) == 2
        names = [r["name"] for r in result]
        assert "pluginA" in names
        assert "pluginB" in names
