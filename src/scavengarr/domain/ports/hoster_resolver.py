"""Port for resolving hoster embed URLs to playable video URLs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from scavengarr.domain.entities.stremio import ResolvedStream


@runtime_checkable
class HosterResolverPort(Protocol):
    """Resolves a hoster embed page URL to an actual video stream URL.

    Implementations handle site-specific extraction logic (JS deobfuscation,
    token generation, API calls, etc.).
    """

    @property
    def name(self) -> str:
        """Hoster name this resolver handles (e.g. 'voe', 'streamtape')."""
        ...

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Resolve a hoster embed URL to a playable video URL.

        Returns None if resolution fails (page offline, extraction broken, etc.).
        """
        ...


@runtime_checkable
class ClientBoundResolverPort(HosterResolverPort, Protocol):
    """A resolver whose CDN binds the video URL to the player's request headers.

    veevcdn answers 403 unless the player sends the User-Agent and the
    Accept-Language the URL was resolved with, and Stremio's streaming
    server passes the browser's Accept-Language on (production,
    2026-10-06). A player's request to ``/play`` carries the headers it then
    sends the CDN, so ``/play`` resolves such a hoster per player.
    """

    @property
    def bound_headers(self) -> tuple[str, ...]:
        """Lower-case names of the request headers the CDN binds the URL to."""
        ...

    async def resolve_for_client(
        self, url: str, headers: Mapping[str, str]
    ) -> ResolvedStream | None:
        """Resolve *url* as the player sending *headers* (lower-case names)."""
        ...
