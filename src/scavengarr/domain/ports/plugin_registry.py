from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PluginRegistryPort(Protocol):
    def discover(self) -> None: ...
    def list_names(self) -> list[str]: ...
    def get(self, name: str): ...
