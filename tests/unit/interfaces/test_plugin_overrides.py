"""Tests for the per-plugin YAML overrides applied in the composition root."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from structlog.testing import capture_logs

from scavengarr.infrastructure.config.schema import PluginOverride
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase
from scavengarr.interfaces.composition import _apply_plugin_overrides


class _HttpxPlugin(HttpxPluginBase):
    name = "h"


class _BrowserPlugin(PlaywrightPluginBase):
    name = "b"


def _apply(plugin: object, override: PluginOverride) -> list[dict[str, object]]:
    registry = MagicMock()
    registry.get.return_value = plugin
    config = SimpleNamespace(plugins=SimpleNamespace(overrides={"p": override}))
    with capture_logs() as logs:
        _apply_plugin_overrides(registry, config)  # type: ignore[arg-type]
    return logs


class TestApplyPluginOverrides:
    def test_httpx_plugin_gets_all_overrides(self) -> None:
        plugin = _HttpxPlugin()

        _apply(plugin, PluginOverride(timeout=7.5, max_concurrent=2, max_results=9))

        assert (plugin._timeout, plugin._max_concurrent, plugin._max_results) == (
            7.5,
            2,
            9,
        )

    def test_browser_plugin_has_no_client_timeout(self) -> None:
        # The timeout was set as an attribute nothing reads
        plugin = _BrowserPlugin()

        logs = _apply(plugin, PluginOverride(timeout=7.5, max_concurrent=2))

        assert plugin._max_concurrent == 2
        assert not hasattr(plugin, "_timeout")
        assert any(e["event"] == "plugin_timeout_override_unsupported" for e in logs)

    def test_unknown_plugin_type_is_reported(self) -> None:
        logs = _apply(object(), PluginOverride(max_results=3))

        assert any(e["event"] == "plugin_override_unsupported" for e in logs)
