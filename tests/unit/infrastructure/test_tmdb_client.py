"""Tests for HttpxTmdbClient (TMDB API adapter)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.infrastructure.tmdb.client import HttpxTmdbClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_API_KEY = "test-api-key-123"
_BASE = "https://api.themoviedb.org/3"


@pytest.fixture()
def cache() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = None  # default: cache miss
    return mock


@pytest.fixture()
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


@pytest.fixture()
def client(http_client: httpx.AsyncClient, cache: AsyncMock) -> HttpxTmdbClient:
    return HttpxTmdbClient(api_key=_API_KEY, http_client=http_client, cache=cache)


# ---------------------------------------------------------------------------
# TMDB JSON response fixtures
# ---------------------------------------------------------------------------

_FIND_MOVIE_RESPONSE = {
    "movie_results": [
        {
            "id": 280,
            "title": "Terminator 2 – Tag der Abrechnung",
            "original_title": "Terminator 2: Judgment Day",
            "overview": "Der Terminator kehrt zurück...",
            "poster_path": "/2y4dmgWYRMYDMWMnappZHcMOAb7.jpg",
            "release_date": "1991-07-03",
            "vote_average": 8.1,
        }
    ],
    "tv_results": [],
    "person_results": [],
}

_FIND_TV_RESPONSE = {
    "movie_results": [],
    "tv_results": [
        {
            "id": 1399,
            "name": "Game of Thrones",
            "original_name": "Game of Thrones",
            "overview": "Sieben Königreiche...",
            "poster_path": "/u3bZgnGQ9T01sWNhyveQz0wH0Hl.jpg",
            "first_air_date": "2011-04-17",
            "vote_average": 8.4,
        }
    ],
    "person_results": [],
}

_FIND_EMPTY_RESPONSE = {
    "movie_results": [],
    "tv_results": [],
    "person_results": [],
}

_TRENDING_MOVIES_RESPONSE = {
    "page": 1,
    "results": [
        {
            "id": 550,
            "title": "Fight Club",
            "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
            "overview": "Ein deprimierter Mann...",
            "release_date": "1999-10-15",
            "vote_average": 8.4,
        },
        {
            "id": 680,
            "title": "Pulp Fiction",
            "poster_path": "/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg",
            "overview": "Vier miteinander verwobene Geschichten...",
            "release_date": "1994-09-10",
            "vote_average": 8.5,
        },
    ],
}

_TRENDING_TV_RESPONSE = {
    "page": 1,
    "results": [
        {
            "id": 94997,
            "name": "Haus des Geldes",
            "poster_path": "/reEMJA1uzscCbkpeRJeTT2bjqUp.jpg",
            "overview": "Ein genialer Plan...",
            "first_air_date": "2017-05-02",
            "vote_average": 8.3,
        },
    ],
}

_SEARCH_MOVIES_RESPONSE = {
    "page": 1,
    "results": [
        {
            "id": 603,
            "title": "Matrix",
            "poster_path": "/f89U3ADr1oiB1s9GkdPOEpXUk5H.jpg",
            "overview": "Der Hacker Neo...",
            "release_date": "1999-03-31",
            "vote_average": 8.2,
        },
    ],
}

_SEARCH_TV_RESPONSE = {
    "page": 1,
    "results": [
        {
            "id": 1396,
            "name": "Breaking Bad",
            "poster_path": "/ggFHVNu6YYI5L9pCfOacjizRGt.jpg",
            "overview": "Walter White...",
            "first_air_date": "2008-01-20",
            "vote_average": 8.9,
        },
    ],
}


# TMDB lists carry no IMDb ids; each title's comes from its external_ids
_IMDB_IDS = {
    ("movie", 550): "tt0137523",
    ("movie", 680): "tt0110912",
    ("movie", 603): "tt0133093",
    ("tv", 94997): "tt6468322",
    ("tv", 1396): "tt0903747",
}


def _mock_imdb_ids() -> None:
    for (endpoint, tmdb_id), imdb_id in _IMDB_IDS.items():
        respx.get(f"{_BASE}/{endpoint}/{tmdb_id}/external_ids").respond(
            json={"id": tmdb_id, "imdb_id": imdb_id}
        )


def _cache_writes(cache: AsyncMock) -> dict[str, tuple[object, object]]:
    """Cache key -> (value, ttl) of every write."""
    return {
        c.args[0]: (c.args[1], c.kwargs.get("ttl")) for c in cache.set.call_args_list
    }


# ---------------------------------------------------------------------------
# find_by_imdb_id
# ---------------------------------------------------------------------------


class TestFindByImdbId:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_movie_found(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        result = await client.find_by_imdb_id("tt0103064")

        assert result is not None
        assert result["title"] == "Terminator 2 – Tag der Abrechnung"
        assert result["id"] == 280
        # Verify cache was written
        cache.set.assert_awaited_once()
        call_args = cache.set.call_args
        assert call_args[0][0] == "tmdb:find:tt0103064:de"
        assert call_args[1]["ttl"] == 86_400

    @respx.mock
    @pytest.mark.asyncio()
    async def test_tv_found(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/find/tt0944947").respond(json=_FIND_TV_RESPONSE)

        result = await client.find_by_imdb_id("tt0944947")

        assert result is not None
        assert result["name"] == "Game of Thrones"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_not_found_returns_none(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt0000000").respond(json=_FIND_EMPTY_RESPONSE)

        result = await client.find_by_imdb_id("tt0000000")

        assert result is None
        cache.set.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_hit_skips_request(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        cached_data = {"id": 280, "title": "Cached Title"}
        cache.get.return_value = cached_data

        route = respx.get(f"{_BASE}/find/tt0103064")

        result = await client.find_by_imdb_id("tt0103064")

        assert result == cached_data
        assert not route.called

    @respx.mock
    @pytest.mark.asyncio()
    async def test_401_returns_none(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt0103064").respond(
            status_code=401, json={"status_message": "Invalid API key"}
        )

        result = await client.find_by_imdb_id("tt0103064")

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_404_returns_none(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt9999999").respond(status_code=404)

        result = await client.find_by_imdb_id("tt9999999")

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_network_error_returns_none(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt0103064").mock(
            side_effect=httpx.ConnectError("DNS failure")
        )

        result = await client.find_by_imdb_id("tt0103064")

        assert result is None


# ---------------------------------------------------------------------------
# trending_movies / trending_tv
# ---------------------------------------------------------------------------


class TestTrendingMovies:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_previews(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/trending/movie/week").respond(
            json=_TRENDING_MOVIES_RESPONSE
        )
        _mock_imdb_ids()

        previews = await client.trending_movies()

        assert len(previews) == 2
        assert previews[0].id == "tt0137523"
        assert previews[0].name == "Fight Club"
        assert previews[0].type == "movie"
        assert (
            previews[0].poster
            == "https://image.tmdb.org/t/p/w500/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg"
        )
        assert previews[0].release_info == "1999"
        assert previews[0].imdb_rating == "8.4"
        assert previews[1].id == "tt0110912"
        assert previews[1].name == "Pulp Fiction"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_pagination(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        route = respx.get(f"{_BASE}/trending/movie/week").respond(
            json=_TRENDING_MOVIES_RESPONSE
        )
        _mock_imdb_ids()

        await client.trending_movies(page=2)

        assert route.called
        request = route.calls[0].request
        assert "page=2" in str(request.url)

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_write(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/trending/movie/week").respond(
            json=_TRENDING_MOVIES_RESPONSE
        )
        _mock_imdb_ids()

        await client.trending_movies()

        _, ttl = _cache_writes(cache)["tmdb:trending:movie:1"]
        assert ttl == 21_600

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_hit(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        from scavengarr.domain.entities.stremio import StremioMetaPreview

        cached = [StremioMetaPreview(id="tt550", type="movie", name="Cached Movie")]
        cache.get.return_value = cached

        route = respx.get(f"{_BASE}/trending/movie/week")

        result = await client.trending_movies()

        assert result == cached
        assert not route.called

    @respx.mock
    @pytest.mark.asyncio()
    async def test_error_returns_empty(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/trending/movie/week").respond(status_code=500)

        result = await client.trending_movies()

        assert result == []


class TestTrendingTv:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_previews(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/trending/tv/week").respond(json=_TRENDING_TV_RESPONSE)
        _mock_imdb_ids()

        previews = await client.trending_tv()

        assert len(previews) == 1
        assert previews[0].id == "tt6468322"
        assert previews[0].name == "Haus des Geldes"
        assert previews[0].type == "series"
        assert previews[0].release_info == "2017"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_write(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/trending/tv/week").respond(json=_TRENDING_TV_RESPONSE)
        _mock_imdb_ids()

        await client.trending_tv()

        assert "tmdb:trending:tv:1" in _cache_writes(cache)


# ---------------------------------------------------------------------------
# search_movies / search_tv
# ---------------------------------------------------------------------------


class TestSearchMovies:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_results(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/movie").respond(json=_SEARCH_MOVIES_RESPONSE)
        _mock_imdb_ids()

        results = await client.search_movies("Matrix")

        assert len(results) == 1
        assert results[0].id == "tt0133093"
        assert results[0].name == "Matrix"
        assert results[0].type == "movie"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_query_param(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        route = respx.get(f"{_BASE}/search/movie").respond(json=_SEARCH_MOVIES_RESPONSE)
        _mock_imdb_ids()

        await client.search_movies("Iron Man", page=2)

        request = route.calls[0].request
        url_str = str(request.url)
        assert "query=Iron" in url_str
        assert "page=2" in url_str

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_key_includes_query(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/movie").respond(json=_SEARCH_MOVIES_RESPONSE)
        _mock_imdb_ids()

        await client.search_movies("Matrix", page=1)

        _, ttl = _cache_writes(cache)["tmdb:search:movie:Matrix:1"]
        assert ttl == 3_600

    @respx.mock
    @pytest.mark.asyncio()
    async def test_error_returns_empty(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/movie").mock(
            side_effect=httpx.ConnectError("timeout")
        )

        result = await client.search_movies("Matrix")

        assert result == []


class TestSearchTv:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_results(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/tv").respond(json=_SEARCH_TV_RESPONSE)
        _mock_imdb_ids()

        results = await client.search_tv("Breaking Bad")

        assert len(results) == 1
        assert results[0].id == "tt0903747"
        assert results[0].name == "Breaking Bad"
        assert results[0].type == "series"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_key(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/search/tv").respond(json=_SEARCH_TV_RESPONSE)
        _mock_imdb_ids()

        await client.search_tv("Breaking Bad", page=3)

        assert "tmdb:search:tv:Breaking Bad:3" in _cache_writes(cache)


class TestCatalogImdbIds:
    """Catalog items carry IMDb ids: Stremio opens an item only through a
    meta addon for its id prefix, and Cinemeta knows ``tt`` ids only."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_titles_without_imdb_id_are_left_out(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/trending/movie/week").respond(
            json=_TRENDING_MOVIES_RESPONSE
        )
        respx.get(f"{_BASE}/movie/550/external_ids").respond(
            json={"id": 550, "imdb_id": "tt0137523"}
        )
        respx.get(f"{_BASE}/movie/680/external_ids").respond(
            json={"id": 680, "imdb_id": None}
        )

        previews = await client.trending_movies()

        assert [p.id for p in previews] == ["tt0137523"]

    @respx.mock
    @pytest.mark.asyncio()
    async def test_imdb_ids_are_cached(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/tv").respond(json=_SEARCH_TV_RESPONSE)
        _mock_imdb_ids()

        await client.search_tv("Breaking Bad")

        value, ttl = _cache_writes(cache)["tmdb:imdb:tv:1396"]
        assert value == "tt0903747"
        assert ttl == 30 * 86_400

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cached_imdb_id_skips_the_lookup(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        cache.get.side_effect = lambda key: (
            "tt0903747" if key == "tmdb:imdb:tv:1396" else None
        )
        respx.get(f"{_BASE}/search/tv").respond(json=_SEARCH_TV_RESPONSE)
        lookup = respx.get(f"{_BASE}/tv/1396/external_ids")

        results = await client.search_tv("Breaking Bad")

        assert [r.id for r in results] == ["tt0903747"]
        assert not lookup.called

    @respx.mock
    @pytest.mark.asyncio()
    async def test_failed_lookup_is_not_cached(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/tv").respond(json=_SEARCH_TV_RESPONSE)
        respx.get(f"{_BASE}/tv/1396/external_ids").respond(status_code=500)

        results = await client.search_tv("Breaking Bad")

        assert results == []
        assert "tmdb:imdb:tv:1396" not in _cache_writes(cache)


# ---------------------------------------------------------------------------
# get_title_by_tmdb_id
# ---------------------------------------------------------------------------


class TestGetTitleByTmdbId:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_movie_title(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/movie/550").respond(
            json={"id": 550, "title": "Fight Club", "name": None}
        )

        result = await client.get_title_by_tmdb_id(550, "movie")

        assert result == "Fight Club"
        cache.set.assert_awaited_once()
        assert cache.set.call_args[0][0] == "tmdb:title:movie:550"
        assert cache.set.call_args[1]["ttl"] == 86_400

    @respx.mock
    @pytest.mark.asyncio()
    async def test_tv_title(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/tv/1396").respond(json={"id": 1396, "name": "Breaking Bad"})

        result = await client.get_title_by_tmdb_id(1396, "series")

        assert result == "Breaking Bad"
        cache.set.assert_awaited_once()
        assert cache.set.call_args[0][0] == "tmdb:title:tv:1396"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_not_found(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        respx.get(f"{_BASE}/movie/999999").respond(status_code=404)

        result = await client.get_title_by_tmdb_id(999999, "movie")

        assert result is None
        cache.set.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cached(self, client: HttpxTmdbClient, cache: AsyncMock) -> None:
        cache.get.return_value = "Cached Title"
        route = respx.get(f"{_BASE}/movie/550")

        result = await client.get_title_by_tmdb_id(550, "movie")

        assert result == "Cached Title"
        assert not route.called

    @respx.mock
    @pytest.mark.asyncio()
    async def test_no_title_in_response(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/movie/888").respond(json={"id": 888})

        result = await client.get_title_by_tmdb_id(888, "movie")

        assert result is None
        cache.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_title_and_year
# ---------------------------------------------------------------------------


class TestGetTitleAndYear:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_movie_returns_title_and_year(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        result = await client.get_title_and_year("tt0103064")

        assert result is not None
        assert result.title == "Terminator 2 – Tag der Abrechnung"
        assert result.year == 1991

    @respx.mock
    @pytest.mark.asyncio()
    async def test_movie_includes_original_title_as_alt(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        """original_title differs from title → included in alt_titles."""
        respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        result = await client.get_title_and_year("tt0103064")

        assert result is not None
        assert "Terminator 2: Judgment Day" in result.alt_titles

    @respx.mock
    @pytest.mark.asyncio()
    async def test_same_original_title_no_alt(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        """When original_title == title, alt_titles is empty."""
        same_title = {
            "movie_results": [
                {
                    "id": 1,
                    "title": "Iron Man",
                    "original_title": "Iron Man",
                    "release_date": "2008-05-02",
                }
            ],
            "tv_results": [],
        }
        respx.get(f"{_BASE}/find/tt0371746").respond(json=same_title)

        result = await client.get_title_and_year("tt0371746")

        assert result is not None
        assert result.alt_titles == []

    @respx.mock
    @pytest.mark.asyncio()
    async def test_tv_returns_title_and_year(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt0944947").respond(json=_FIND_TV_RESPONSE)

        result = await client.get_title_and_year("tt0944947")

        assert result is not None
        assert result.title == "Game of Thrones"
        assert result.year == 2011

    @respx.mock
    @pytest.mark.asyncio()
    async def test_not_found_returns_none(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/find/tt0000000").respond(json=_FIND_EMPTY_RESPONSE)

        result = await client.get_title_and_year("tt0000000")

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_missing_date_returns_none_year(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        no_date = {
            "movie_results": [{"id": 1, "title": "No Date Movie"}],
            "tv_results": [],
        }
        respx.get(f"{_BASE}/find/tt0000001").respond(json=no_date)

        result = await client.get_title_and_year("tt0000001")

        assert result is not None
        assert result.title == "No Date Movie"
        assert result.year is None


# ---------------------------------------------------------------------------
# Language parameter
# ---------------------------------------------------------------------------


class TestLanguageParameter:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_find_by_imdb_id_uses_language_tag(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        """find_by_imdb_id passes language tag to TMDB API."""
        route = respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        await client.find_by_imdb_id("tt0103064", language="en")

        request = route.calls[0].request
        assert "language=en-EN" in str(request.url)

    @respx.mock
    @pytest.mark.asyncio()
    async def test_find_by_imdb_id_cache_key_includes_language(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        """Cache key includes language to avoid cross-language collisions."""
        respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        await client.find_by_imdb_id("tt0103064", language="en")

        call_args = cache.set.call_args
        assert call_args[0][0] == "tmdb:find:tt0103064:en"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_get_title_and_year_passes_language(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        """get_title_and_year forwards language to find_by_imdb_id."""
        route = respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        await client.get_title_and_year("tt0103064", language="en")

        request = route.calls[0].request
        assert "language=en-EN" in str(request.url)

    @respx.mock
    @pytest.mark.asyncio()
    async def test_default_language_is_german(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        """Default language is de-DE (German)."""
        route = respx.get(f"{_BASE}/find/tt0103064").respond(json=_FIND_MOVIE_RESPONSE)

        await client.find_by_imdb_id("tt0103064")

        request = route.calls[0].request
        assert "language=de-DE" in str(request.url)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_missing_poster_path(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        response = {
            "page": 1,
            "results": [
                {
                    "id": 999,
                    "title": "No Poster Movie",
                    "poster_path": None,
                    "overview": "",
                    "release_date": "",
                    "vote_average": 0,
                }
            ],
        }
        respx.get(f"{_BASE}/trending/movie/week").respond(json=response)
        respx.get(f"{_BASE}/movie/999/external_ids").respond(
            json={"id": 999, "imdb_id": "tt0000999"}
        )

        previews = await client.trending_movies()

        assert len(previews) == 1
        assert previews[0].id == "tt0000999"
        assert previews[0].poster == ""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_missing_vote_average(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        response = {
            "page": 1,
            "results": [
                {
                    "id": 999,
                    "title": "No Rating",
                    "poster_path": "/abc.jpg",
                    "overview": "",
                    "release_date": "2024-01-01",
                    "vote_average": 0,
                }
            ],
        }
        respx.get(f"{_BASE}/trending/movie/week").respond(json=response)
        respx.get(f"{_BASE}/movie/999/external_ids").respond(
            json={"id": 999, "imdb_id": "tt0000999"}
        )

        previews = await client.trending_movies()

        assert previews[0].imdb_rating == ""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_empty_results_list(
        self, client: HttpxTmdbClient, cache: AsyncMock
    ) -> None:
        respx.get(f"{_BASE}/search/movie").respond(json={"page": 1, "results": []})

        result = await client.search_movies("xyznonexistent")

        assert result == []
        # Empty list should still be cached
        cache.set.assert_awaited_once()
