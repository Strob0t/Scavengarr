"""Port for plugin discovery and access."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from scavengarr.domain.plugins.base import PluginProtocol
from scavengarr.domain.plugins.plugin_schema import YamlPluginDefinition


@runtime_checkable
class PluginRegistryPort(Protocol):
    """Synchronous interface for plugin discovery, listing, and retrieval."""

    def discover(self) -> None: ...
    def list_names(self) -> list[str]: ...
    def get(self, name: str) -> YamlPluginDefinition | PluginProtocol: ...
