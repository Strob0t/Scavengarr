"""Unit tests for QueryPoolBuilder (IMDB Suggest based)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from scavengarr.infrastructure.scoring.query_pool import (
    _FALLBACK_MOVIES,
    _FALLBACK_TV,
    QueryPoolBuilder,
    _date_range_y1_2,
    _date_range_y5_10,
    _year_in_range,
)

_SUGGEST_BASE = "https://v2.sg.media-imdb.com/suggestion"


def _make_builder(
    mock_cache: AsyncMock, clock: _Clock | None = None
) -> QueryPoolBuilder:
    if clock is None:
        return QueryPoolBuilder(http_client=httpx.AsyncClient(), cache=mock_cache)
    return QueryPoolBuilder(
        http_client=httpx.AsyncClient(), cache=mock_cache, clock=clock
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _suggest_response(entries: list[dict]) -> dict:
    """Build an IMDB Suggest API response."""
    return {"d": entries}


def _movie_entry(title: str, year: int) -> dict:
    return {
        "l": title,
        "y": year,
        "qid": "movie",
        "id": f"tt{abs(hash(title)) % 10**7:07d}",
    }


def _tv_entry(title: str, year: int) -> dict:
    return {
        "l": title,
        "y": year,
        "qid": "tvSeries",
        "id": f"tt{abs(hash(title)) % 10**7:07d}",
    }


class TestGetQueries:
    @respx.mock
    async def test_returns_titles_from_imdb_suggest(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        now = datetime.now(timezone.utc)

        # Mock all suggest endpoints to return current-year movies.
        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200,
            json=_suggest_response(
                [_movie_entry(f"Film {i}", now.year) for i in range(5)]
            ),
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=3)

        assert len(result) == 3
        for t in result:
            assert t.startswith("Film")

    @respx.mock
    async def test_returns_fallback_on_imdb_failure(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        respx.get(url__startswith=_SUGGEST_BASE).respond(500)
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t in _FALLBACK_MOVIES

    @respx.mock
    async def test_uses_cached_pool(self, mock_cache: AsyncMock) -> None:
        titles = ["Cached Film 1", "Cached Film 2", "Cached Film 3"]
        mock_cache.get = AsyncMock(return_value=json.dumps(titles))
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t in titles

    @respx.mock
    async def test_tv_category_filters_tv_entries(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        now = datetime.now(timezone.utc)

        # Return mix of movies and TV — only TV should be collected.
        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200,
            json=_suggest_response(
                [
                    _movie_entry("Some Movie", now.year),
                    _tv_entry("Serie A", now.year),
                    _tv_entry("Serie B", now.year),
                ]
            ),
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(5000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t.startswith("Serie")

    @respx.mock
    async def test_y1_2_filters_by_year_range(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        now = datetime.now(timezone.utc)

        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200,
            json=_suggest_response(
                [
                    _movie_entry("Too New", now.year),
                    _movie_entry("In Range", now.year - 1),
                    _movie_entry("Too Old", now.year - 5),
                ]
            ),
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "y1_2", count=10)

        assert "In Range" in result
        assert "Too New" not in result
        assert "Too Old" not in result

    @respx.mock
    async def test_y5_10_filters_by_year_range(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        now = datetime.now(timezone.utc)

        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200,
            json=_suggest_response(
                [
                    _movie_entry("Too New", now.year - 2),
                    _movie_entry("In Range", now.year - 7),
                    _movie_entry("Too Old", now.year - 15),
                ]
            ),
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "y5_10", count=10)

        assert "In Range" in result
        assert "Too New" not in result
        assert "Too Old" not in result

    @respx.mock
    async def test_deterministic_rotation_same_week(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        now = datetime.now(timezone.utc)
        entries = [_movie_entry(f"Film {chr(65 + i)}", now.year) for i in range(20)]

        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200, json=_suggest_response(entries)
        )
        builder = _make_builder(mock_cache)

        result1 = await builder.get_queries(2000, "current", count=3)

        # Reset cache to force re-fetch.
        mock_cache.get = AsyncMock(return_value=None)

        result2 = await builder.get_queries(2000, "current", count=3)

        assert result1 == result2

    async def test_tv_fallback_pool(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        builder = _make_builder(mock_cache)

        with respx.mock:
            respx.get(url__startswith=_SUGGEST_BASE).respond(500)
            result = await builder.get_queries(5000, "current", count=2)

        assert len(result) == 2
        for t in result:
            assert t in _FALLBACK_TV

    @respx.mock
    async def test_deduplicates_titles(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        now = datetime.now(timezone.utc)

        # Same title from every suggest query.
        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200,
            json=_suggest_response([_movie_entry("Dune", now.year)]),
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=5)

        assert result == ["Dune"]

    @respx.mock
    async def test_skips_entries_without_year(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)

        respx.get(url__startswith=_SUGGEST_BASE).respond(
            200,
            json=_suggest_response(
                [
                    {"l": "No Year Film", "qid": "movie", "id": "tt0000001"},
                ]
            ),
        )
        builder = _make_builder(mock_cache)

        result = await builder.get_queries(2000, "current", count=5)

        # No year means it can't match any bucket range.
        assert "No Year Film" not in result


class TestUnavailable:
    """Production: the network gone for 33 h, the scheduler's tick found
    every one of 108 cells due, and each rebuilt its pool with 10 IMDb
    requests, each logged with a traceback (287 000 records in 24 h)."""

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("Temporary failure in name resolution"),
            # a resolver that does not answer: each request waits 10 s
            httpx.ConnectTimeout("timed out"),
        ],
        ids=["connect-error", "connect-timeout"],
    )
    async def test_a_build_stops_at_the_first_connect_error(
        self, mock_cache: AsyncMock, error: httpx.TransportError
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        builder = _make_builder(mock_cache, _Clock())

        with respx.mock, capture_logs() as logs:
            route = respx.get(url__startswith=_SUGGEST_BASE).mock(side_effect=error)
            result = await builder.get_queries(2000, "current", count=2)

        assert result == []
        assert route.call_count == 1
        unavailable = [e for e in logs if e["event"] == "query_pool_unavailable"]
        assert len(unavailable) == 1
        assert unavailable[0]["log_level"] == "warning"
        assert "exc_info" not in unavailable[0]
        assert unavailable[0]["error"] == type(error).__name__
        assert not [e for e in logs if e["event"] == "query_pool_imdb_error"]
        mock_cache.set.assert_not_awaited()

    async def test_no_fetch_within_the_cooldown(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        clock = _Clock()
        builder = _make_builder(mock_cache, clock)
        now = datetime.now(timezone.utc)

        with respx.mock, capture_logs() as logs:
            route = respx.get(url__startswith=_SUGGEST_BASE).mock(
                side_effect=httpx.ConnectError("refused")
            )
            await builder.get_queries(2000, "current")
            clock.now += 599
            assert await builder.get_queries(5000, "y1_2") == []
            assert route.call_count == 1

            route.mock(
                side_effect=None,
                return_value=httpx.Response(
                    200, json=_suggest_response([_movie_entry("Dune", now.year)])
                ),
            )
            clock.now += 2
            assert await builder.get_queries(2000, "current") == ["Dune"]

        assert route.call_count == 11
        assert len([e for e in logs if e["event"] == "query_pool_unavailable"]) == 1


class TestDateRanges:
    def test_y1_2_range(self) -> None:
        lo, hi = _date_range_y1_2()
        assert hi - lo == 1

    def test_y5_10_range(self) -> None:
        lo, hi = _date_range_y5_10()
        assert hi - lo == 5


class TestYearInRange:
    def test_in_range(self) -> None:
        assert _year_in_range(2024, 2020, 2025) is True

    def test_below_range(self) -> None:
        assert _year_in_range(2019, 2020, 2025) is False

    def test_above_range(self) -> None:
        assert _year_in_range(2026, 2020, 2025) is False

    def test_none_year(self) -> None:
        assert _year_in_range(None, 2020, 2025) is False

    def test_boundary_inclusive(self) -> None:
        assert _year_in_range(2020, 2020, 2025) is True
        assert _year_in_range(2025, 2020, 2025) is True


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
