"""Port for the translation of anime catalog ids into the pipeline's requests.

Anime catalogs in Stremio hand out ``kitsu:<id>`` and ``kitsu:<id>:<episode>``
with Kitsu's numbering; the pipeline searches by IMDb id, season and episode.
The anime sites number a long-runner's episodes as Kitsu does, so an IMDb
request's episode gets its Kitsu number too.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from scavengarr.domain.entities.stremio import StremioStreamRequest


@runtime_checkable
class AnimeIdResolverPort(Protocol):
    """Translates a ``kitsu:`` stream request into a ``tt`` request and
    numbers an IMDb anime episode as Kitsu does."""

    async def translate(
        self, request: StremioStreamRequest
    ) -> StremioStreamRequest | None:
        """The request with the IMDb id, content type, season and episode of
        *request*'s title as IMDb counts them; ``None`` when no source maps
        the id (the caller answers without streams)."""
        ...

    async def kitsu_episode(self, request: StremioStreamRequest) -> int | None:
        """The number of an IMDb series request's episode in the one Kitsu
        entry that counts the series from its start (One Piece's S21E109 is
        1000); ``None`` for other requests, for a series no entry counts
        whole and while the sources do not answer. Waits for no list
        download."""
        ...

    async def has_whole_series_entry(self, imdb_id: str) -> bool | None:
        """Whether a Kitsu entry counts the series *imdb_id* from its start
        (One Piece's does, Duel Monsters has an entry per season); ``None``
        while the sources cannot say. Where none does, the catalog's
        position numbers an episode as the sites do, as far as anything
        knows; where one does, only its number is sure. Waits for no list
        download."""
        ...

    async def aclose(self) -> None:
        """End the background work still running (at shutdown)."""
        ...


class _NoAnimeIds:
    """Translates nothing: the default where no resolver is wired in."""

    async def translate(
        self, request: StremioStreamRequest
    ) -> StremioStreamRequest | None:
        del request
        return None

    async def kitsu_episode(self, request: StremioStreamRequest) -> int | None:
        del request
        return None

    async def has_whole_series_entry(self, imdb_id: str) -> bool | None:
        del imdb_id
        return False

    async def aclose(self) -> None:
        return None


NO_ANIME_IDS: AnimeIdResolverPort = _NoAnimeIds()
