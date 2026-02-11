"""Tests for ImdbFallbackClient (IMDB Suggest API title resolver)."""

from __future__ import annotations

import json

import httpx
import pytest

from scavengarr.infrastructure.tmdb.imdb_fallback import ImdbFallbackClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IRON_MAN_RESPONSE = json.dumps(
    {
        "d": [
            {
                "i": {"height": 2048, "imageUrl": "https://example.com/im.jpg", "width": 1382},
                "id": "tt0371746",
                "l": "Iron Man",
                "q": "feature",
                "qid": "movie",
                "rank": 996,
                "s": "Robert Downey Jr., Gwyneth Paltrow",
                "y": 2008,
            }
        ],
        "q": "tt0371746",
        "v": 1,
    }
)

_EMPTY_RESPONSE = json.dumps({"d": [], "q": "tt9999999", "v": 1})


class _FakeCache:
    """Minimal async cache for testing."""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    async def get(self, key: str) -> object | None:
        return self._data.get(key)

    async def set(self, key: str, value: object, *, ttl: int = 0) -> None:
        self._data[key] = value

    async def clear(self) -> None:
        self._data.clear()


def _make_client(
    response_text: str = _IRON_MAN_RESPONSE,
    status_code: int = 200,
) -> tuple[ImdbFallbackClient, _FakeCache]:
    """Build a client backed by a fake transport and cache."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code, text=response_text)
    )
    http = httpx.AsyncClient(transport=transport)
    cache = _FakeCache()
    client = ImdbFallbackClient(http_client=http, cache=cache)
    return client, cache


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetGermanTitle:
    @pytest.mark.asyncio
    async def test_resolves_title(self) -> None:
        client, _ = _make_client()
        title = await client.get_german_title("tt0371746")
        assert title == "Iron Man"

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_response(self) -> None:
        client, _ = _make_client(response_text=_EMPTY_RESPONSE)
        assert await client.get_german_title("tt9999999") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        client, _ = _make_client(status_code=500)
        assert await client.get_german_title("tt0371746") is None

    @pytest.mark.asyncio
    async def test_result_is_cached(self) -> None:
        client, cache = _make_client()
        await client.get_german_title("tt0371746")

        # Verify the cache was populated
        cached = await cache.get("imdb:suggest:tt0371746")
        assert cached is not None
        assert cached["l"] == "Iron Man"

        # Second call should use cache (even if transport would fail)
        title = await client.get_german_title("tt0371746")
        assert title == "Iron Man"


class TestFindByImdbId:
    @pytest.mark.asyncio
    async def test_returns_dict_with_title_keys(self) -> None:
        client, _ = _make_client()
        result = await client.find_by_imdb_id("tt0371746")
        assert result is not None
        assert result["title"] == "Iron Man"
        assert result["name"] == "Iron Man"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self) -> None:
        client, _ = _make_client(response_text=_EMPTY_RESPONSE)
        assert await client.find_by_imdb_id("tt9999999") is None


class TestUnsupportedMethods:
    @pytest.mark.asyncio
    async def test_tmdb_id_returns_none(self) -> None:
        client, _ = _make_client()
        assert await client.get_title_by_tmdb_id(1726, "movie") is None

    @pytest.mark.asyncio
    async def test_trending_movies_empty(self) -> None:
        client, _ = _make_client()
        assert await client.trending_movies() == []

    @pytest.mark.asyncio
    async def test_trending_tv_empty(self) -> None:
        client, _ = _make_client()
        assert await client.trending_tv() == []

    @pytest.mark.asyncio
    async def test_search_movies_empty(self) -> None:
        client, _ = _make_client()
        assert await client.search_movies("Iron Man") == []

    @pytest.mark.asyncio
    async def test_search_tv_empty(self) -> None:
        client, _ = _make_client()
        assert await client.search_tv("Breaking Bad") == []
