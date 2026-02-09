# src/scavengarr/plugins/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class SearchResult:
    """Normalized search result."""

    title: str
    download_link: str

    # Torznab-Standard-Felder
    seeders: Optional[int] = None
    leechers: Optional[int] = None
    size: Optional[str] = None

    # Erweiterte Felder
    release_name: Optional[str] = None
    description: Optional[str] = None
    published_date: Optional[str] = None

    # Multi-stage specific
    download_links: Optional[List[Dict[str, str]]] = None
    source_url: Optional[str] = None
    scraped_from_stage: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Torznab-spezifisch
    category: int = 2000  # Default: Movies
    grabs: int = 0
    download_volume_factor: float = 0.0  # Direct Download = kein Upload nötig
    upload_volume_factor: float = 0.0


@dataclass
class StageResult:
    """
    Internal result from a single scraping stage.

    Used during multi-stage processing before final normalization.
    """

    url: str
    stage_name: str
    depth: int
    data: Dict[str, Any]
    links: List[str] = field(default_factory=list)


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


class MultiStagePluginProtocol(Protocol):
    """
    Extended protocol for multi-stage plugins.

    Supports progressive data collection across multiple page levels.
    """

    name: str

    async def search(
        self, query: str, category: int | None = None
    ) -> list[SearchResult]: ...

    async def scrape_stage(
        self,
        stage_name: str,
        url: Optional[str] = None,
        depth: int = 0,
        **url_params: Any,
    ) -> list[StageResult]: ...
