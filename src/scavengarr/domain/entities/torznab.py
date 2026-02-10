from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TorznabAction = Literal["caps", "search"]


@dataclass(frozen=True)
class TorznabQuery:
    action: str  # "search", "caps", etc.
    plugin_name: str  # Plugin identifier (e.g., "filmpalast")
    query: str  # Search query string

    # Optional filters
    category: int | None = None  # Torznab category (2000=Movies, 5000=TV)

    # Extended search parameters (Prowlarr)
    extended: int | None = None  # 1 = extended search mode

    # Pagination (future)
    offset: int | None = None
    limit: int | None = None


@dataclass(frozen=True)
class TorznabItem:
    title: str
    download_url: str
    job_id: str | None = None
    seeders: int | None = None
    peers: int | None = None
    size: str | None = None
    # Extended fields
    release_name: str | None = None
    description: str | None = None
    source_url: str | None = None  # Detail page URL
    # Torznab-specific
    category: int = 2000  # Default: Movies
    grabs: int = 0
    download_volume_factor: float = 0.0  # 0 = Direct Download
    upload_volume_factor: float = 0.0


@dataclass(frozen=True)
class TorznabCaps:
    server_title: str
    server_version: str
    limits_max: int = 100
    limits_default: int = 50


@dataclass(frozen=True)
class TorznabIndexInfo:
    name: str
    version: str | None
    mode: str | None


class TorznabError(Exception):
    """Base error for Torznab domain/usecases."""


class TorznabBadRequest(TorznabError):
    pass


class TorznabUnsupportedAction(TorznabError):
    pass


class TorznabNoPluginsAvailable(TorznabError):
    pass


class TorznabPluginNotFound(TorznabError):
    pass


class TorznabUnsupportedPlugin(TorznabError):
    pass


class TorznabExternalError(TorznabError):
    """Network / parsing / upstream errors (plugin external dependency)."""
