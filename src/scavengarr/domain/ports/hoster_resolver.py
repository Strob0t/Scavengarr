"""Port for resolving hoster embed URLs to playable video URLs."""

from __future__ import annotations

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
