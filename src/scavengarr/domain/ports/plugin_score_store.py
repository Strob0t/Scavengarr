"""Port for plugin score persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from scavengarr.domain.entities.scoring import PluginScoreSnapshot


@runtime_checkable
class PluginScoreStorePort(Protocol):
    """Async interface for storing and querying plugin score snapshots."""

    async def get_snapshot(
        self, plugin: str, category: int, bucket: str
    ) -> PluginScoreSnapshot | None: ...

    async def put_snapshot(self, snapshot: PluginScoreSnapshot) -> None: ...

    async def list_snapshots(
        self, plugin: str | None = None
    ) -> list[PluginScoreSnapshot]: ...

    async def get_last_run(
        self,
        probe_type: str,
        plugin: str,
        category: int | None = None,
        bucket: str | None = None,
    ) -> datetime | None: ...

    async def set_last_run(
        self,
        probe_type: str,
        plugin: str,
        ts: datetime,
        category: int | None = None,
        bucket: str | None = None,
    ) -> None: ...
