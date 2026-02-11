"""Port for stream link persistence."""

from __future__ import annotations

from typing import Protocol

from scavengarr.domain.entities.stremio import CachedStreamLink


class StreamLinkRepository(Protocol):
    """Async interface for storing and retrieving cached stream links."""

    async def save(self, link: CachedStreamLink) -> None: ...

    async def get(self, stream_id: str) -> CachedStreamLink | None: ...
