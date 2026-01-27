from __future__ import annotations

from typing import Optional, Protocol

from pydantic import BaseModel


class SearchResult(BaseModel):
    """
    Normalized search result produced by plugins.

    This is intentionally minimal for phase 1.
    """

    title: str
    download_link: str
    seeders: Optional[int] = None
    leechers: Optional[int] = None
    size: Optional[str] = None
    published_date: Optional[str] = None


class PluginProtocol(Protocol):
    """
    Protocol for Python plugins.

    A Python plugin must export a module-level variable named `plugin` that:
    - has a `name: str` attribute
    - implements: async def search(self, query: str, category: int | None = None) -> list[SearchResult]
    """

    name: str

    async def search(
        self, query: str, category: int | None = None
    ) -> list[SearchResult]: ...
