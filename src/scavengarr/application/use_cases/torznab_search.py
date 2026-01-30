from __future__ import annotations

from scavengarr.domain.entities import (
    TorznabBadRequest,
    TorznabExternalError,
    TorznabItem,
    TorznabPluginNotFound,
    TorznabQuery,
    TorznabUnsupportedPlugin,
)
from scavengarr.domain.ports import PluginRegistryPort
from scavengarr.domain.ports.search_engine import SearchEnginePort


class TorznabSearchUseCase:
    def __init__(
        self, *, plugins: PluginRegistryPort, engine: SearchEnginePort
    ) -> None:
        self._plugins = plugins
        self._engine = engine

    async def execute(self, q: TorznabQuery) -> list[TorznabItem]:
        if q.action != "search":
            raise TorznabBadRequest("TorznabSearchUseCase only supports action=search")
        if not q.query:
            raise TorznabBadRequest("Missing query parameter 'q'")
        if not q.plugin_name:
            raise TorznabBadRequest("Missing plugin name")

        self._plugins.discover()
        try:
            plugin = self._plugins.get(q.plugin_name)
        except Exception as e:
            raise TorznabPluginNotFound(q.plugin_name) from e

        # For now we support YAML plugins with scraping.mode == "scrapy".
        try:
            mode = plugin.scraping.mode  # type: ignore[attr-defined]
        except Exception as e:
            raise TorznabUnsupportedPlugin(
                "Plugin does not expose scraping.mode"
            ) from e
        if mode != "scrapy":
            raise TorznabUnsupportedPlugin(f"Unsupported scraping.mode: {mode}")

        try:
            results = await self._engine.search(plugin, q.query)
        except TorznabExternalError:
            raise
        except Exception as e:
            raise TorznabExternalError(str(e)) from e

        items: list[TorznabItem] = []
        for r in results:
            items.append(
                TorznabItem(
                    title=getattr(r, "title"),
                    download_url=getattr(r, "download_link"),
                    seeders=getattr(r, "seeders", None),
                    peers=getattr(r, "leechers", None),
                    size=getattr(r, "size", None),
                )
            )
        return items
