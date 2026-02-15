"""Unit tests for QueryPoolBuilder."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from scavengarr.infrastructure.scoring.query_pool import (
    QueryPoolBuilder,
    _FALLBACK_MOVIES,
    _FALLBACK_TV,
    _date_range_y1_2,
    _date_range_y5_10,
)

_TMDB_BASE = "https://api.themoviedb.org/3"


def _make_builder(
    mock_cache: AsyncMock,
) -> QueryPoolBuilder:
    return QueryPoolBuilder(
        api_key="test-key",
        http_client=httpx.AsyncClient(),
        cache=mock_cache,
    )


def _movie_results(titles: list[str]) -> dict:
    return {
        "results": [{"title": t, "id": i} for i, t in enumerate(titles)]
    }


def _tv_results(titles: list[str]) -> dict:
    return {
        "results": [{"name": t, "id": i} for i, t in enumerate(titles)]
    }


class TestGetQueries:
    @respx.mock
    async def test_returns_titles_from_trending(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        titles = [f"Film {i}" for i in range(20)]
        respx.get(f"{_TMDB_BASE}/trending/movie/week").respond(
            200, json=_movie_results(titles)
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=3)

        assert len(result) == 3
        for t in result:
            assert t.startswith("Film")

    @respx.mock
    async def test_returns_fallback_on_tmdb_failure(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        respx.get(f"{_TMDB_BASE}/trending/movie/week").respond(500)
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t in _FALLBACK_MOVIES

    @respx.mock
    async def test_uses_cached_pool(
        self, mock_cache: AsyncMock
    ) -> None:
        titles = ["Cached Film 1", "Cached Film 2", "Cached Film 3"]
        mock_cache.get = AsyncMock(return_value=json.dumps(titles))
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t in titles

    @respx.mock
    async def test_tv_category_uses_tv_endpoint(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        titles = ["Serie A", "Serie B", "Serie C"]
        respx.get(f"{_TMDB_BASE}/trending/tv/week").respond(
            200, json=_tv_results(titles)
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(5000, "current", count=2)

        assert len(result) == 2

    @respx.mock
    async def test_discover_y1_2_uses_date_range(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        titles = ["Discover Film 1", "Discover Film 2"]
        respx.get(f"{_TMDB_BASE}/discover/movie").respond(
            200, json=_movie_results(titles)
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "y1_2", count=2)

        assert len(result) == 2
        # Verify date params were sent.
        req = respx.calls[0].request
        assert b"primary_release_date.gte" in req.url.query
        assert b"primary_release_date.lte" in req.url.query

    @respx.mock
    async def test_discover_y5_10_uses_date_range(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        titles = ["Old Film 1", "Old Film 2"]
        respx.get(f"{_TMDB_BASE}/discover/movie").respond(
            200, json=_movie_results(titles)
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "y5_10", count=2)

        assert len(result) == 2

    @respx.mock
    async def test_deterministic_rotation_same_week(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        titles = [f"Film {chr(65 + i)}" for i in range(20)]
        respx.get(f"{_TMDB_BASE}/trending/movie/week").respond(
            200, json=_movie_results(titles)
        )
        builder = _make_builder(mock_cache)

        # Two calls with same week should give same results.
        result1 = await builder.get_queries(2000, "current", count=3)

        # Reset cache to force re-fetch for second call.
        mock_cache.get = AsyncMock(return_value=None)
        respx.get(f"{_TMDB_BASE}/trending/movie/week").respond(
            200, json=_movie_results(titles)
        )

        result2 = await builder.get_queries(2000, "current", count=3)

        assert result1 == result2

    async def test_tv_fallback_pool(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        builder = _make_builder(mock_cache)

        # Force fallback by not mocking any HTTP.
        with respx.mock:
            respx.get(f"{_TMDB_BASE}/trending/tv/week").respond(500)
            result = await builder.get_queries(5000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t in _FALLBACK_TV


class TestDateRanges:
    def test_y1_2_range(self) -> None:
        gte, lte = _date_range_y1_2()
        assert gte.endswith("-01-01")
        assert lte.endswith("-12-31")
        gte_year = int(gte[:4])
        lte_year = int(lte[:4])
        assert lte_year - gte_year == 1

    def test_y5_10_range(self) -> None:
        gte, lte = _date_range_y5_10()
        assert gte.endswith("-01-01")
        assert lte.endswith("-12-31")
        gte_year = int(gte[:4])
        lte_year = int(lte[:4])
        assert lte_year - gte_year == 5


class TestMediaTypeMapping:
    def test_movie_category(self) -> None:
        builder = _make_builder(AsyncMock())
        assert builder._media_type(2000) == "movie"

    def test_tv_category(self) -> None:
        builder = _make_builder(AsyncMock())
        assert builder._media_type(5000) == "tv"

    def test_tv_subcategory(self) -> None:
        builder = _make_builder(AsyncMock())
        assert builder._media_type(5040) == "tv"

    def test_other_category_defaults_to_movie(self) -> None:
        builder = _make_builder(AsyncMock())
        assert builder._media_type(1000) == "movie"
