"""Tests for StremioCatalogUseCase."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scavengarr.application.use_cases.stremio_catalog import StremioCatalogUseCase
from scavengarr.domain.entities.stremio import StremioMetaPreview


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MOVIE_PREVIEWS = [
    StremioMetaPreview(
        id="tt0137523",
        type="movie",
        name="Fight Club",
        poster="https://image.tmdb.org/t/p/w500/poster.jpg",
        description="Ein deprimierter Mann...",
        release_info="1999",
        imdb_rating="8.4",
    ),
    StremioMetaPreview(
        id="tt0110912",
        type="movie",
        name="Pulp Fiction",
        poster="https://image.tmdb.org/t/p/w500/pulp.jpg",
        description="Vier Geschichten...",
        release_info="1994",
        imdb_rating="8.5",
    ),
]

_TV_PREVIEWS = [
    StremioMetaPreview(
        id="tt2930604",
        type="series",
        name="Haus des Geldes",
        poster="https://image.tmdb.org/t/p/w500/money.jpg",
        description="Ein genialer Plan...",
        release_info="2017",
        imdb_rating="8.3",
    ),
]


@pytest.fixture()
def mock_tmdb() -> AsyncMock:
    tmdb = AsyncMock()
    tmdb.trending_movies = AsyncMock(return_value=_MOVIE_PREVIEWS)
    tmdb.trending_tv = AsyncMock(return_value=_TV_PREVIEWS)
    tmdb.search_movies = AsyncMock(return_value=_MOVIE_PREVIEWS[:1])
    tmdb.search_tv = AsyncMock(return_value=_TV_PREVIEWS)
    return tmdb


@pytest.fixture()
def use_case(mock_tmdb: AsyncMock) -> StremioCatalogUseCase:
    return StremioCatalogUseCase(tmdb=mock_tmdb)


# ---------------------------------------------------------------------------
# Trending
# ---------------------------------------------------------------------------


class TestTrending:
    async def test_trending_movies(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        result = await use_case.trending("movie")

        assert len(result) == 2
        assert result[0].name == "Fight Club"
        assert result[1].name == "Pulp Fiction"
        mock_tmdb.trending_movies.assert_awaited_once_with(page=1)
        mock_tmdb.trending_tv.assert_not_awaited()

    async def test_trending_series(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        result = await use_case.trending("series")

        assert len(result) == 1
        assert result[0].name == "Haus des Geldes"
        assert result[0].type == "series"
        mock_tmdb.trending_tv.assert_awaited_once_with(page=1)
        mock_tmdb.trending_movies.assert_not_awaited()

    async def test_trending_pagination(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        await use_case.trending("movie", page=3)

        mock_tmdb.trending_movies.assert_awaited_once_with(page=3)

    async def test_trending_error_returns_empty(
        self, mock_tmdb: AsyncMock
    ) -> None:
        mock_tmdb.trending_movies.side_effect = RuntimeError("TMDB down")
        uc = StremioCatalogUseCase(tmdb=mock_tmdb)

        result = await uc.trending("movie")

        assert result == []

    async def test_trending_tv_error_returns_empty(
        self, mock_tmdb: AsyncMock
    ) -> None:
        mock_tmdb.trending_tv.side_effect = RuntimeError("timeout")
        uc = StremioCatalogUseCase(tmdb=mock_tmdb)

        result = await uc.trending("series")

        assert result == []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_movies(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        result = await use_case.search("movie", "Fight Club")

        assert len(result) == 1
        assert result[0].name == "Fight Club"
        mock_tmdb.search_movies.assert_awaited_once_with(query="Fight Club", page=1)
        mock_tmdb.search_tv.assert_not_awaited()

    async def test_search_series(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        result = await use_case.search("series", "Haus des Geldes")

        assert len(result) == 1
        assert result[0].name == "Haus des Geldes"
        mock_tmdb.search_tv.assert_awaited_once_with(
            query="Haus des Geldes", page=1
        )
        mock_tmdb.search_movies.assert_not_awaited()

    async def test_search_pagination(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        await use_case.search("movie", "Matrix", page=2)

        mock_tmdb.search_movies.assert_awaited_once_with(query="Matrix", page=2)

    async def test_search_empty_query_returns_empty(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        result = await use_case.search("movie", "")

        assert result == []
        mock_tmdb.search_movies.assert_not_awaited()

    async def test_search_whitespace_query_returns_empty(
        self, use_case: StremioCatalogUseCase, mock_tmdb: AsyncMock
    ) -> None:
        result = await use_case.search("series", "   ")

        assert result == []
        mock_tmdb.search_tv.assert_not_awaited()

    async def test_search_error_returns_empty(
        self, mock_tmdb: AsyncMock
    ) -> None:
        mock_tmdb.search_movies.side_effect = RuntimeError("API error")
        uc = StremioCatalogUseCase(tmdb=mock_tmdb)

        result = await uc.search("movie", "Matrix")

        assert result == []

    async def test_search_tv_error_returns_empty(
        self, mock_tmdb: AsyncMock
    ) -> None:
        mock_tmdb.search_tv.side_effect = RuntimeError("timeout")
        uc = StremioCatalogUseCase(tmdb=mock_tmdb)

        result = await uc.search("series", "Breaking Bad")

        assert result == []


# ---------------------------------------------------------------------------
# Return type integrity
# ---------------------------------------------------------------------------


class TestReturnTypes:
    async def test_trending_returns_stremio_meta_previews(
        self, use_case: StremioCatalogUseCase
    ) -> None:
        result = await use_case.trending("movie")

        for item in result:
            assert isinstance(item, StremioMetaPreview)

    async def test_search_returns_stremio_meta_previews(
        self, use_case: StremioCatalogUseCase
    ) -> None:
        result = await use_case.search("movie", "Fight Club")

        for item in result:
            assert isinstance(item, StremioMetaPreview)

    async def test_trending_empty_from_tmdb(
        self, mock_tmdb: AsyncMock
    ) -> None:
        mock_tmdb.trending_movies.return_value = []
        uc = StremioCatalogUseCase(tmdb=mock_tmdb)

        result = await uc.trending("movie")

        assert result == []

    async def test_search_no_results_from_tmdb(
        self, mock_tmdb: AsyncMock
    ) -> None:
        mock_tmdb.search_movies.return_value = []
        uc = StremioCatalogUseCase(tmdb=mock_tmdb)

        result = await uc.search("movie", "xyznonexistent")

        assert result == []
