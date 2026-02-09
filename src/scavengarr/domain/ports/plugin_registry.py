from __future__ import annotations

from typing import Protocol, runtime_checkable

from scavengarr.domain.plugins.schema import YamlPluginDefinition


@runtime_checkable
class PluginRegistryPort(Protocol):
    def discover(self) -> None: ...
    def list_names(self) -> list[str]: ...
    def get(self, name: str) -> YamlPluginDefinition: ...
