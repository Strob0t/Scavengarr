"""Stremio catalog use case — trending and search via TMDB."""

from __future__ import annotations

import structlog

from scavengarr.domain.entities.stremio import StremioContentType, StremioMetaPreview
from scavengarr.domain.ports.tmdb import TmdbClientPort

log = structlog.get_logger(__name__)


class StremioCatalogUseCase:
    """Provides Stremio catalog data (trending + search) backed by TMDB.

    Delegates all API/cache logic to the injected TmdbClientPort.
    The use case is responsible for content-type dispatching and error handling.
    """

    def __init__(self, tmdb: TmdbClientPort) -> None:
        self._tmdb = tmdb

    async def trending(
        self,
        content_type: StremioContentType,
        page: int = 1,
    ) -> list[StremioMetaPreview]:
        """Fetch trending items for a content type.

        Args:
            content_type: ``"movie"`` or ``"series"``.
            page: TMDB page number (1-based).

        Returns:
            List of catalog previews (may be empty on error).
        """
        try:
            if content_type == "movie":
                return await self._tmdb.trending_movies(page=page)
            return await self._tmdb.trending_tv(page=page)
        except Exception:
            log.warning(
                "stremio_catalog_trending_error",
                content_type=content_type,
                page=page,
                exc_info=True,
            )
            return []

    async def search(
        self,
        content_type: StremioContentType,
        query: str,
        page: int = 1,
    ) -> list[StremioMetaPreview]:
        """Search TMDB catalog by query.

        Args:
            content_type: ``"movie"`` or ``"series"``.
            query: Search query string.
            page: TMDB page number (1-based).

        Returns:
            List of matching previews (may be empty on error or no results).
        """
        if not query.strip():
            return []

        try:
            if content_type == "movie":
                return await self._tmdb.search_movies(query=query, page=page)
            return await self._tmdb.search_tv(query=query, page=page)
        except Exception:
            log.warning(
                "stremio_catalog_search_error",
                content_type=content_type,
                query=query,
                page=page,
                exc_info=True,
            )
            return []
