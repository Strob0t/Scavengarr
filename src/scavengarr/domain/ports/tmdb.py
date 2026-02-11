"""Port for TMDB API operations."""

from __future__ import annotations

from typing import Protocol

from scavengarr.domain.entities.stremio import StremioMetaPreview


class TmdbClientPort(Protocol):
    """Async interface for TMDB API lookups."""

    async def find_by_imdb_id(self, imdb_id: str) -> dict | None:
        """Lookup TMDB entry by IMDb ID.

        Returns movie/TV metadata dict or None if not found.
        """
        ...

    async def get_german_title(self, imdb_id: str) -> str | None:
        """Get the German title for an IMDb ID.

        Returns None if not found.
        """
        ...

    async def trending_movies(self, page: int = 1) -> list[StremioMetaPreview]:
        """Fetch trending movies (German locale)."""
        ...

    async def trending_tv(self, page: int = 1) -> list[StremioMetaPreview]:
        """Fetch trending TV shows (German locale)."""
        ...

    async def search_movies(
        self, query: str, page: int = 1
    ) -> list[StremioMetaPreview]:
        """Search movies by query (German locale)."""
        ...

    async def search_tv(self, query: str, page: int = 1) -> list[StremioMetaPreview]:
        """Search TV shows by query (German locale)."""
        ...
