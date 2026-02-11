"""Port for multi-stage search execution."""

from __future__ import annotations

from typing import Any, Protocol

from scavengarr.domain.plugins.base import SearchResult


class SearchEnginePort(Protocol):
    """Async interface for executing plugin-driven search pipelines."""

    async def search(
        self, plugin: Any, query: str, **params: Any
    ) -> list[SearchResult]: ...

    async def validate_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]: ...
