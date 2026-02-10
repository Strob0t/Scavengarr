"""Port for multi-stage search execution."""

from __future__ import annotations

from typing import Protocol

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort


class SearchEnginePort(Protocol):
    """Async interface for executing plugin-driven search pipelines."""

    async def search(
        self, plugin: PluginRegistryPort, query: str
    ) -> list[SearchResult]: ...
