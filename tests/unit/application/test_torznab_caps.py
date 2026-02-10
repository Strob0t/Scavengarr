"""Tests for TorznabCapsUseCase."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scavengarr.application.use_cases.torznab_caps import TorznabCapsUseCase
from scavengarr.domain.entities import TorznabPluginNotFound


class TestTorznabCapsUseCase:
    def test_returns_caps_with_server_title(
        self,
        mock_plugin_registry: MagicMock,
    ) -> None:
        uc = TorznabCapsUseCase(
            plugins=mock_plugin_registry,
            app_name="scavengarr",
            plugin_name="filmpalast",
            server_version="0.1.0",
        )
        caps = uc.execute()
        assert caps.server_title == "scavengarr (filmpalast)"
        assert caps.server_version == "0.1.0"

    def test_raises_plugin_not_found(self) -> None:
        registry = MagicMock()
        registry.get.side_effect = KeyError("not found")

        uc = TorznabCapsUseCase(
            plugins=registry,
            app_name="scavengarr",
            plugin_name="nonexistent",
            server_version="0.1.0",
        )
        with pytest.raises(TorznabPluginNotFound):
            uc.execute()

    def test_uses_plugin_name_attribute(
        self,
        mock_plugin_registry: MagicMock,
    ) -> None:
        uc = TorznabCapsUseCase(
            plugins=mock_plugin_registry,
            app_name="app",
            plugin_name="filmpalast",
            server_version="1.0",
        )
        caps = uc.execute()
        assert "filmpalast" in caps.server_title
