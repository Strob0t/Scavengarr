"""Port for search result validation."""

from __future__ import annotations

from typing import Protocol

from scavengarr.domain.plugins.base import SearchResult


class SearchEnginePort(Protocol):
    """Async interface for validating search results."""

    async def validate_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]: ...
