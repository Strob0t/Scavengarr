"""Port for the translation of anime catalog ids into the pipeline's requests.

Anime catalogs in Stremio hand out ``kitsu:<id>`` and ``kitsu:<id>:<episode>``
with Kitsu's numbering; the pipeline searches by IMDb id, season and episode.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from scavengarr.domain.entities.stremio import StremioStreamRequest


@runtime_checkable
class AnimeIdResolverPort(Protocol):
    """Translates a ``kitsu:`` stream request into a ``tt`` request."""

    async def translate(
        self, request: StremioStreamRequest
    ) -> StremioStreamRequest | None:
        """The request with the IMDb id, content type, season and episode of
        *request*'s title as IMDb counts them; ``None`` when no source maps
        the id (the caller answers without streams)."""
        ...


class _NoAnimeIds:
    """Translates nothing: the default where no resolver is wired in."""

    async def translate(
        self, request: StremioStreamRequest
    ) -> StremioStreamRequest | None:
        del request
        return None


NO_ANIME_IDS: AnimeIdResolverPort = _NoAnimeIds()
