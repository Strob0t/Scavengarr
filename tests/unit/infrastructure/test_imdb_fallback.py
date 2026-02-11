"""Tests for ImdbFallbackClient (IMDB Suggest API + Wikidata title resolver)."""

from __future__ import annotations

import json
from collections.abc import Callable

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
                "i": {
                    "height": 2048,
                    "imageUrl": "https://example.com/im.jpg",
                    "width": 1382,
                },
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

_SHAWSHANK_RESPONSE = json.dumps(
    {
        "d": [
            {
                "id": "tt0111161",
                "l": "The Shawshank Redemption",
                "q": "feature",
                "qid": "movie",
                "y": 1994,
            }
        ],
        "q": "tt0111161",
        "v": 1,
    }
)

_WIKIDATA_SEARCH_Q172241 = json.dumps(
    {
        "batchcomplete": "",
        "query": {
            "searchinfo": {"totalhits": 1},
            "search": [{"ns": 0, "title": "Q172241", "pageid": 172357}],
        },
    }
)

_WIKIDATA_ENTITY_Q172241_DE = json.dumps(
    {
        "entities": {
            "Q172241": {
                "type": "item",
                "id": "Q172241",
                "labels": {"de": {"language": "de", "value": "Die Verurteilten"}},
            }
        },
        "success": 1,
    }
)

_WIKIDATA_EMPTY_SEARCH = json.dumps(
    {"batchcomplete": "", "query": {"searchinfo": {"totalhits": 0}, "search": []}}
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
    """Build a client backed by a fake transport (single response) and cache.

    Wikidata calls return empty search results so they don't interfere
    with tests that don't care about German titles.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if "wikidata.org" in str(request.url):
            return httpx.Response(200, text=_WIKIDATA_EMPTY_SEARCH)
        return httpx.Response(status_code, text=response_text)

    transport = httpx.MockTransport(_handler)
    http = httpx.AsyncClient(transport=transport)
    cache = _FakeCache()
    client = ImdbFallbackClient(http_client=http, cache=cache)
    return client, cache


def _make_client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[ImdbFallbackClient, _FakeCache]:
    """Build a client with a custom request handler."""
    transport = httpx.MockTransport(handler)
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

    @pytest.mark.asyncio
    async def test_returns_wikidata_german_title_when_available(self) -> None:
        """get_german_title prefers Wikidata German title over IMDB English."""

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "wikidata.org" in url and "wbgetentities" in url:
                return httpx.Response(200, text=_WIKIDATA_ENTITY_Q172241_DE)
            if "wikidata.org" in url:
                return httpx.Response(200, text=_WIKIDATA_SEARCH_Q172241)
            return httpx.Response(200, text=_SHAWSHANK_RESPONSE)

        client, _ = _make_client_with_handler(_handler)
        title = await client.get_german_title("tt0111161")
        assert title == "Die Verurteilten"


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


class TestGetTitleAndYear:
    @pytest.mark.asyncio
    async def test_returns_title_and_year(self) -> None:
        client, _ = _make_client()
        result = await client.get_title_and_year("tt0371746")
        assert result is not None
        assert result.title == "Iron Man"
        assert result.year == 2008

    @pytest.mark.asyncio
    async def test_missing_year_field(self) -> None:
        no_year = json.dumps(
            {"d": [{"id": "tt0000001", "l": "Some Title"}], "q": "tt0000001", "v": 1}
        )
        client, _ = _make_client(response_text=no_year)
        result = await client.get_title_and_year("tt0000001")
        assert result is not None
        assert result.title == "Some Title"
        assert result.year is None

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self) -> None:
        client, _ = _make_client(response_text=_EMPTY_RESPONSE)
        assert await client.get_title_and_year("tt9999999") is None

    @pytest.mark.asyncio
    async def test_wikidata_german_title_becomes_primary(self) -> None:
        """When Wikidata returns a German title, it becomes primary."""

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "wikidata.org" in url and "wbgetentities" in url:
                return httpx.Response(200, text=_WIKIDATA_ENTITY_Q172241_DE)
            if "wikidata.org" in url:
                return httpx.Response(200, text=_WIKIDATA_SEARCH_Q172241)
            return httpx.Response(200, text=_SHAWSHANK_RESPONSE)

        client, _ = _make_client_with_handler(_handler)
        result = await client.get_title_and_year("tt0111161")
        assert result is not None
        assert result.title == "Die Verurteilten"
        assert result.year == 1994
        assert "The Shawshank Redemption" in result.alt_titles

    @pytest.mark.asyncio
    async def test_wikidata_failure_falls_back_to_english(self) -> None:
        """When Wikidata fails, English title is returned without alts."""

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "wikidata.org" in url:
                return httpx.Response(500, text="error")
            return httpx.Response(200, text=_SHAWSHANK_RESPONSE)

        client, _ = _make_client_with_handler(_handler)
        result = await client.get_title_and_year("tt0111161")
        assert result is not None
        assert result.title == "The Shawshank Redemption"
        assert result.year == 1994
        assert result.alt_titles == []

    @pytest.mark.asyncio
    async def test_wikidata_no_german_label_falls_back(self) -> None:
        """When Wikidata entity exists but has no German label."""
        entity_no_de = json.dumps(
            {
                "entities": {
                    "Q172241": {
                        "type": "item",
                        "id": "Q172241",
                        "labels": {},
                    }
                },
                "success": 1,
            }
        )

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "wikidata.org" in url and "wbgetentities" in url:
                return httpx.Response(200, text=entity_no_de)
            if "wikidata.org" in url:
                return httpx.Response(200, text=_WIKIDATA_SEARCH_Q172241)
            return httpx.Response(200, text=_SHAWSHANK_RESPONSE)

        client, _ = _make_client_with_handler(_handler)
        result = await client.get_title_and_year("tt0111161")
        assert result is not None
        assert result.title == "The Shawshank Redemption"
        assert result.alt_titles == []

    @pytest.mark.asyncio
    async def test_wikidata_same_title_no_alt(self) -> None:
        """When German title equals English, no alt_titles are added."""
        entity_same = json.dumps(
            {
                "entities": {
                    "Q172241": {
                        "type": "item",
                        "id": "Q172241",
                        "labels": {"de": {"language": "de", "value": "Iron Man"}},
                    }
                },
                "success": 1,
            }
        )

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "wikidata.org" in url and "wbgetentities" in url:
                return httpx.Response(200, text=entity_same)
            if "wikidata.org" in url:
                return httpx.Response(200, text=_WIKIDATA_SEARCH_Q172241)
            return httpx.Response(200, text=_IRON_MAN_RESPONSE)

        client, _ = _make_client_with_handler(_handler)
        result = await client.get_title_and_year("tt0371746")
        assert result is not None
        assert result.title == "Iron Man"
        assert result.alt_titles == []

    @pytest.mark.asyncio
    async def test_wikidata_result_is_cached(self) -> None:
        """Wikidata German title is cached after first successful lookup."""
        call_count = {"wikidata": 0}

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "wikidata.org" in url and "wbgetentities" in url:
                call_count["wikidata"] += 1
                return httpx.Response(200, text=_WIKIDATA_ENTITY_Q172241_DE)
            if "wikidata.org" in url:
                call_count["wikidata"] += 1
                return httpx.Response(200, text=_WIKIDATA_SEARCH_Q172241)
            return httpx.Response(200, text=_SHAWSHANK_RESPONSE)

        client, _ = _make_client_with_handler(_handler)

        # First call: hits Wikidata
        result1 = await client.get_title_and_year("tt0111161")
        assert result1 is not None
        assert result1.title == "Die Verurteilten"
        first_calls = call_count["wikidata"]
        assert first_calls >= 2  # search + entity

        # Second call: should use cache for both IMDB and Wikidata
        result2 = await client.get_title_and_year("tt0111161")
        assert result2 is not None
        assert result2.title == "Die Verurteilten"
        # No new Wikidata calls
        assert call_count["wikidata"] == first_calls


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
