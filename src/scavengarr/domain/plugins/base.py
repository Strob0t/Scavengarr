"""Domain models and protocols for the plugin system."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

PluginProvides = Literal["stream", "download", "both"]


@dataclass
class SearchResult:
    """Normalized search result.

    ``metadata`` keys with a meaning beyond the plugin: ``season`` and
    ``episode`` (ints) name the episode a series result is for, set by
    plugins that know it from the page; the Stremio episode filter reads
    them before guessing from the title. ``source_plugin`` is set by the
    search runner.
    """

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


def link_url(link: Mapping[str, str]) -> str:
    """The URL of one of a result's ``download_links``: plugins store it
    under ``link``, older ones under ``url``."""
    return (link.get("link") or link.get("url") or "").strip()


# Plugin, title, release, link, links
ResultKey = tuple[str, str, str | None, str, tuple[str, ...]]


def result_key(result: SearchResult) -> ResultKey:
    """What makes a result the same one found again: plugin, release, links."""
    links = tuple(map(link_url, result.download_links or ()))
    return (
        result.metadata.get("source_plugin", ""),
        result.title,
        result.release_name,
        result.download_link,
        links,
    )


class PluginUnreachableError(Exception):
    """None of the plugin's domains answered its domain check: the search
    cannot start. The next search checks the domains again."""

    def __init__(self, plugin: str) -> None:
        super().__init__(f"{plugin}: no domain reachable")
        self.plugin = plugin


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


@runtime_checkable
class GrabResolvingPlugin(Protocol):
    """Optional plugin capability: resolve download links at grab time.

    For sites whose real links cost a captcha or count against a download
    quota: search results keep a page URL, and the download endpoint calls
    ``resolve_download`` only when an Arr app actually grabs the result.
    """

    async def resolve_download(self, url: str) -> list[str]:
        """Return the download links behind *url* (empty list if none)."""
        ...
