from __future__ import annotations

from typing import Protocol

from scavengarr.domain.plugins.base import SearchResult


class SearchEnginePort(Protocol):
    async def search(self, plugin, query: str) -> list[SearchResult]: ...
