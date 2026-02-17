"""Port for stream link persistence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from scavengarr.domain.entities.stremio import CachedStreamLink


@runtime_checkable
class StreamLinkRepository(Protocol):
    """Async interface for storing and retrieving cached stream links."""

    async def save(self, link: CachedStreamLink) -> None: ...

    async def get(self, stream_id: str) -> CachedStreamLink | None: ...
