from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

TorznabAction = Literal["caps", "search"]


@dataclass(frozen=True)
class TorznabQuery:
    action: TorznabAction
    plugin_name: str
    query: str | None = None


@dataclass(frozen=True)
class TorznabItem:
    title: str
    download_url: str
    seeders: int | None = None
    peers: int | None = None
    size: str | None = None
    # Extended fields
    release_name: Optional[str] = None
    description: Optional[str] = None
    source_url: Optional[str] = None  # Detail page URL
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
