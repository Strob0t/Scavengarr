"""Use case for listing all available Torznab indexers."""

from __future__ import annotations

from scavengarr.domain.entities import TorznabIndexInfo
from scavengarr.domain.ports import PluginRegistryPort


class TorznabIndexersUseCase:
    """Collects metadata from all discovered plugins."""

    def __init__(self, *, plugins: PluginRegistryPort) -> None:
        self._plugins = plugins

    def execute(self) -> list[dict]:
        out: list[dict] = []

        for name in self._plugins.list_names():
            version = None
            mode = None
            try:
                p = self._plugins.get(name)
                version = getattr(p, "version", None)
                scraping = getattr(p, "scraping", None)
                mode = getattr(scraping, "mode", None) if scraping is not None else None
                if mode is None:
                    mode = getattr(p, "mode", None)
            except Exception:
                pass  # Resilient: broken plugins still appear with minimal info

            info = TorznabIndexInfo(name=name, version=version, mode=mode)
            out.append({"name": info.name, "version": info.version, "mode": info.mode})

        return out
