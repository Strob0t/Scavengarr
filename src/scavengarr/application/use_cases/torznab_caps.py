from __future__ import annotations

from scavengarr.domain.entities import TorznabCaps, TorznabPluginNotFound
from scavengarr.domain.ports import PluginRegistryPort


class TorznabCapsUseCase:
    def __init__(
        self,
        *,
        plugins: PluginRegistryPort,
        app_name: str,
        plugin_name: str,
        server_version: str,
    ) -> None:
        self._plugins = plugins
        self._app_name = app_name
        self._plugin_name = plugin_name
        self._server_version = server_version

    def execute(self) -> TorznabCaps:
        try:
            plugin = self._plugins.get(self._plugin_name)
        except Exception as e:
            raise TorznabPluginNotFound(self._plugin_name) from e

        # If plugin has a title/name, prefer that; otherwise use path param.
        plugin_title = getattr(plugin, "name", self._plugin_name)
        return TorznabCaps(
            server_title=f"{self._app_name} ({plugin_title})",
            server_version=self._server_version,
        )
