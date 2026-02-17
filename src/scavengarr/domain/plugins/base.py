"""Domain models and protocols for the plugin system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

PluginProvides = Literal["stream", "download", "both"]


@dataclass
class SearchResult:
    """Normalized search result."""

    title: str
    download_link: str

    # Torznab standard fields
    seeders: int | None = None
    leechers: int | None = None
    size: str | None = None

    # Extended fields
    release_name: str | None = None
    description: str | None = None
    published_date: str | None = None

    # Multi-stage specific
    download_links: list[dict[str, str]] | None = None
    source_url: str | None = None
    scraped_from_stage: str | None = None

    # Post-validation: all valid URLs (primary + alternatives)
    validated_links: list[str] | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Torznab-specific
    category: int = 2000  # Default: Movies
    grabs: int = 0
    download_volume_factor: float = 0.0  # Direct Download = no upload required
    upload_volume_factor: float = 0.0


class PluginProtocol(Protocol):
    """
    Protocol for Python plugins.

    A Python plugin must export a module-level variable named `plugin` that:
    - has a `name: str` attribute
    - implements: async def search(query, category, season,
      episode) returning list[SearchResult]
    """

    name: str
    provides: PluginProvides

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]: ...
