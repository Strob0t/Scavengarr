"""Unit tests for the cineby plugin."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "cineby.py"


@pytest.fixture()
def cineby_mod():
    """Import cineby plugin module."""
    spec = importlib.util.spec_from_file_location("cineby", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cineby"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("cineby", None)


# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------

SEARCH_MULTI_RESPONSE = {
    "page": 1,
    "total_pages": 2,
    "total_results": 25,
    "results": [
        {
            "id": 414906,
            "title": "The Batman",
            "media_type": "movie",
            "overview": "In his second year of fighting crime, "
            "Batman uncovers corruption.",
            "poster_path": "/74xTEgt7R36Fpooo50r9T25onhq.jpg",
            "release_date": "2022-03-01",
            "vote_average": 7.7,
            "genre_ids": [80, 9648, 53],
            "original_language": "en",
        },
        {
            "id": 2098,
            "name": "Batman: The Animated Series",
            "media_type": "tv",
            "overview": "Vowing to avenge the murder of his parents.",
            "poster_path": "/lBomQFW1vlm1yUYMNSbFZ45R4Ox.jpg",
            "first_air_date": "1992-09-05",
            "vote_average": 8.6,
            "genre_ids": [10759, 16, 18, 9648],
            "original_language": "en",
        },
        {
            "id": 12345,
            "name": "Some Person",
            "media_type": "person",
            "known_for_department": "Acting",
        },
    ],
}

SEARCH_MULTI_PAGE_2 = {
    "page": 2,
    "total_pages": 2,
    "total_results": 25,
    "results": [
        {
            "id": 272,
            "title": "Batman Begins",
            "media_type": "movie",
            "overview": "Driven by tragedy, Bruce Wayne dedicates his life.",
            "poster_path": "/sPX89Td70IDDjVr85jdSBb4rWGr.jpg",
            "release_date": "2005-06-10",
            "vote_average": 7.7,
            "genre_ids": [18, 80, 28],
            "original_language": "en",
        },
    ],
}

SEARCH_MOVIE_RESPONSE = {
    "page": 1,
    "total_pages": 1,
    "total_results": 1,
    "results": [
        {
            "id": 414906,
            "title": "The Batman",
            "overview": "In his second year of fighting crime.",
            "poster_path": "/74xTEgt7R36Fpooo50r9T25onhq.jpg",
            "release_date": "2022-03-01",
            "vote_average": 7.7,
            "genre_ids": [80, 9648, 53],
            "original_language": "en",
        },
    ],
}

SEARCH_TV_RESPONSE = {
    "page": 1,
    "total_pages": 1,
    "total_results": 1,
    "results": [
        {
            "id": 2098,
            "name": "Batman: The Animated Series",
            "overview": "Vowing to avenge the murder of his parents.",
            "poster_path": "/lBomQFW1vlm1yUYMNSbFZ45R4Ox.jpg",
            "first_air_date": "1992-09-05",
            "vote_average": 8.6,
            "genre_ids": [10759, 16, 18, 9648],
            "original_language": "en",
        },
    ],
}

SEARCH_EMPTY_RESPONSE: dict = {
    "page": 1,
    "total_pages": 0,
    "total_results": 0,
    "results": [],
}

MOVIE_DETAIL_RESPONSE = {
    "id": 414906,
    "title": "The Batman",
    "imdb_id": "tt1877830",
    "overview": "In his second year of fighting crime...",
    "runtime": 176,
    "genres": [
        {"id": 80, "name": "Crime"},
        {"id": 9648, "name": "Mystery"},
        {"id": 53, "name": "Thriller"},
    ],
    "release_date": "2022-03-01",
    "vote_average": 7.656,
    "poster_path": "/74xTEgt7R36Fpooo50r9T25onhq.jpg",
}

TV_DETAIL_RESPONSE = {
    "id": 2098,
    "name": "Batman: The Animated Series",
    "imdb_id": "tt0103359",
    "overview": "Vowing to avenge the murder of his parents.",
    "episode_run_time": [22],
    "genres": [
        {"id": 10759, "name": "Action & Adventure"},
        {"id": 16, "name": "Animation"},
        {"id": 18, "name": "Drama"},
    ],
    "first_air_date": "1992-09-05",
    "vote_average": 8.559,
    "poster_path": "/lBomQFW1vlm1yUYMNSbFZ45R4Ox.jpg",
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_json_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = json.dumps(data)
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, cineby_mod):
        assert cineby_mod.plugin.name == "cineby"

    def test_provides(self, cineby_mod):
        assert cineby_mod.plugin.provides == "stream"

    def test_version(self, cineby_mod):
        assert cineby_mod.plugin.version == "1.0.0"

    def test_mode(self, cineby_mod):
        assert cineby_mod.plugin.mode == "httpx"

    def test_domains(self, cineby_mod):
        domains = cineby_mod.plugin._domains
        assert "cineby.gd" in domains
        assert "cineby.app" in domains
        assert "cineby.xyz" in domains
        assert len(domains) >= 8

    def test_categories(self, cineby_mod):
        cats = cineby_mod.plugin.categories
        assert 2000 in cats
        assert 5000 in cats


# ---------------------------------------------------------------------------
# Build search result tests
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    """Tests for _build_search_result method."""

    def test_movie_result(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]  # movie
        detail = MOVIE_DETAIL_RESPONSE

        sr = p._build_search_result(entry, detail)

        assert sr.title == "The Batman (2022)"
        assert sr.download_link == "https://www.vidking.net/embed/movie/414906"
        assert sr.category == 2000
        assert sr.published_date == "2022-03-01"

    def test_tv_result(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][1]  # tv
        detail = TV_DETAIL_RESPONSE

        sr = p._build_search_result(entry, detail)

        assert sr.title == "Batman: The Animated Series (1992)"
        assert sr.download_link == "https://www.vidking.net/embed/tv/2098"
        assert sr.category == 5000
        assert sr.published_date == "1992-09-05"

    def test_tv_with_season_episode(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][1]

        sr = p._build_search_result(entry, None, season=2, episode=5)

        assert sr.download_link == "https://www.vidking.net/embed/tv/2098/2/5"

    def test_tv_with_season_only(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][1]

        sr = p._build_search_result(entry, None, season=3)

        assert sr.download_link == "https://www.vidking.net/embed/tv/2098/3/1"

    def test_no_detail_fallback(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert sr.title == "The Batman (2022)"
        assert sr.metadata["imdb_id"] == ""
        assert sr.metadata["runtime"] == ""

    def test_source_url(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert sr.source_url == "https://www.cineby.gd/movie/414906"

    def test_metadata_genres_from_search(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert "Crime" in sr.metadata["genres"]
        assert "Mystery" in sr.metadata["genres"]
        assert "Thriller" in sr.metadata["genres"]

    def test_metadata_genres_from_detail(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]
        detail = MOVIE_DETAIL_RESPONSE

        sr = p._build_search_result(entry, detail)

        # Detail genres should override search genre IDs
        assert sr.metadata["genres"] == "Crime, Mystery, Thriller"

    def test_metadata_imdb_id(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]
        detail = MOVIE_DETAIL_RESPONSE

        sr = p._build_search_result(entry, detail)

        assert sr.metadata["imdb_id"] == "tt1877830"

    def test_metadata_tmdb_id(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert sr.metadata["tmdb_id"] == "414906"

    def test_metadata_rating(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert sr.metadata["rating"] == "7.7"

    def test_metadata_runtime_movie(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]
        detail = MOVIE_DETAIL_RESPONSE

        sr = p._build_search_result(entry, detail)

        assert sr.metadata["runtime"] == "176"

    def test_metadata_runtime_tv(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][1]
        detail = TV_DETAIL_RESPONSE

        sr = p._build_search_result(entry, detail)

        assert sr.metadata["runtime"] == "22"

    def test_metadata_poster(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = SEARCH_MULTI_RESPONSE["results"][0]

        sr = p._build_search_result(entry, None)

        assert sr.metadata["poster"].startswith("https://image.tmdb.org/t/p/w185/")

    def test_no_year_in_title(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = {
            "id": 999,
            "title": "Unknown Movie",
            "media_type": "movie",
            "release_date": "",
        }

        sr = p._build_search_result(entry, None)

        assert sr.title == "Unknown Movie"
        assert sr.published_date is None

    def test_long_description_truncated(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = {
            "id": 999,
            "title": "Long Desc",
            "media_type": "movie",
            "overview": "A" * 500,
            "release_date": "2023-01-01",
        }

        sr = p._build_search_result(entry, None)

        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_no_poster(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        entry = {
            "id": 999,
            "title": "No Poster",
            "media_type": "movie",
            "poster_path": "",
            "release_date": "",
        }

        sr = p._build_search_result(entry, None)

        assert sr.metadata["poster"] == ""


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for CinebyPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, cineby_mod, mock_client):
        p = cineby_mod.CinebyPlugin()
        p._client = mock_client
        p.base_url = "https://www.cineby.gd"
        return p

    @pytest.mark.asyncio
    async def test_search_returns_movie_and_tv(self, plugin, mock_client):
        single_page = {
            **SEARCH_MULTI_RESPONSE,
            "total_pages": 1,
        }
        search_resp = _make_json_response(single_page)
        movie_detail = _make_json_response(MOVIE_DETAIL_RESPONSE)
        tv_detail = _make_json_response(TV_DETAIL_RESPONSE)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/3/search/multi" in url_str:
                return search_resp
            if "/3/movie/414906" in url_str:
                return movie_detail
            if "/3/tv/2098" in url_str:
                return tv_detail
            return _make_json_response({})

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        # Person results filtered out → 2 results
        assert len(results) == 2
        assert results[0].title == "The Batman (2022)"
        assert results[0].category == 2000
        assert results[1].title == "Batman: The Animated Series (1992)"
        assert results[1].category == 5000

    @pytest.mark.asyncio
    async def test_search_pagination(self, plugin, mock_client):
        """Search fetches multiple pages when available."""
        page_count = 0

        async def mock_get(url, **kwargs):
            nonlocal page_count
            url_str = str(url)
            if "/3/search/multi" in url_str:
                page_count += 1
                if "page=1" in url_str:
                    return _make_json_response(SEARCH_MULTI_RESPONSE)
                return _make_json_response(SEARCH_MULTI_PAGE_2)
            if "/3/movie/" in url_str:
                return _make_json_response(MOVIE_DETAIL_RESPONSE)
            if "/3/tv/" in url_str:
                return _make_json_response(TV_DETAIL_RESPONSE)
            return _make_json_response({})

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert page_count == 2
        # Page 1: 2 results (1 movie + 1 tv, person filtered)
        # Page 2: 1 movie
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_empty_query(self, plugin):
        results = await plugin.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_movie_category_filter(self, plugin, mock_client):
        """Category 2000 searches movies only."""
        search_resp = _make_json_response(SEARCH_MOVIE_RESPONSE)
        detail_resp = _make_json_response(MOVIE_DETAIL_RESPONSE)

        captured_urls: list[str] = []

        async def mock_get(url, **kwargs):
            url_str = str(url)
            captured_urls.append(url_str)
            if "/3/search/" in url_str:
                return search_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman", category=2000)

        assert len(results) == 1
        # Should use /3/search/movie, not /3/search/multi
        search_urls = [u for u in captured_urls if "/3/search/" in u]
        assert any("/3/search/movie" in u for u in search_urls)

    @pytest.mark.asyncio
    async def test_search_tv_category_filter(self, plugin, mock_client):
        """Category 5000 searches TV only."""
        search_resp = _make_json_response(SEARCH_TV_RESPONSE)
        detail_resp = _make_json_response(TV_DETAIL_RESPONSE)

        captured_urls: list[str] = []

        async def mock_get(url, **kwargs):
            url_str = str(url)
            captured_urls.append(url_str)
            if "/3/search/" in url_str:
                return search_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman", category=5000)

        assert len(results) == 1
        search_urls = [u for u in captured_urls if "/3/search/" in u]
        assert any("/3/search/tv" in u for u in search_urls)

    @pytest.mark.asyncio
    async def test_search_rejected_category(self, plugin):
        """Non-movie/TV categories return empty."""
        results = await plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin, mock_client):
        search_resp = _make_json_response(SEARCH_EMPTY_RESPONSE)
        mock_client.get = AsyncMock(return_value=search_resp)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin.search("batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_failure_still_returns_result(self, plugin, mock_client):
        """When detail fails, result uses search entry data only."""
        single_search = {
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
            "results": [SEARCH_MULTI_RESPONSE["results"][0]],
        }
        search_resp = _make_json_response(single_search)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/3/search/" in url_str:
                return search_resp
            # Detail fails
            raise httpx.ConnectError("Connection refused")

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 1
        assert results[0].title == "The Batman (2022)"
        assert results[0].metadata["imdb_id"] == ""

    @pytest.mark.asyncio
    async def test_entry_without_id_skipped(self, plugin, mock_client):
        """Entries without an id field should be skipped."""
        bad_search = {
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
            "results": [
                {
                    "title": "No ID Movie",
                    "media_type": "movie",
                    "release_date": "2023-01-01",
                },
            ],
        }
        search_resp = _make_json_response(bad_search)
        mock_client.get = AsyncMock(return_value=search_resp)

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_person_results_filtered(self, plugin, mock_client):
        """Person results from multi-search should be excluded."""
        person_only = {
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
            "results": [
                {
                    "id": 12345,
                    "name": "Some Person",
                    "media_type": "person",
                    "known_for_department": "Acting",
                },
            ],
        }
        search_resp = _make_json_response(person_only)
        mock_client.get = AsyncMock(return_value=search_resp)

        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_stops_on_last_page(self, plugin, mock_client):
        """Search stops paginating when current page >= total pages."""
        single_page = {
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
            "results": [SEARCH_MULTI_RESPONSE["results"][0]],
        }
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            url_str = str(url)
            if "/3/search/" in url_str:
                call_count += 1
                return _make_json_response(single_page)
            return _make_json_response(MOVIE_DETAIL_RESPONSE)

        mock_client.get = AsyncMock(side_effect=mock_get)

        await plugin.search("batman")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_season_episode_in_tv_embed_url(self, plugin, mock_client):
        """Season and episode should be included in TV embed URLs."""
        search_resp = _make_json_response(SEARCH_TV_RESPONSE)
        detail_resp = _make_json_response(TV_DETAIL_RESPONSE)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/3/search/" in url_str:
                return search_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman", category=5000, season=1, episode=3)

        assert len(results) == 1
        assert "/embed/tv/2098/1/3" in results[0].download_link

    @pytest.mark.asyncio
    async def test_typed_search_injects_media_type(self, plugin, mock_client):
        """Movie-only search should inject media_type into results."""
        search_resp = _make_json_response(SEARCH_MOVIE_RESPONSE)
        detail_resp = _make_json_response(MOVIE_DETAIL_RESPONSE)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/3/search/" in url_str:
                return search_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman", category=2000)

        assert len(results) == 1
        assert results[0].category == 2000
        assert "embed/movie/" in results[0].download_link

    @pytest.mark.asyncio
    async def test_detail_fetch_capped(self, cineby_mod, mock_client):
        """Only first _MAX_DETAIL_FETCH results get detail-fetched."""
        max_detail = cineby_mod._MAX_DETAIL_FETCH  # 25

        # Build 40 movie entries (exceeds cap)
        entries = [
            {
                "id": 1000 + i,
                "title": f"Movie {i}",
                "media_type": "movie",
                "release_date": "2023-01-01",
                "overview": f"Overview {i}",
                "poster_path": "",
                "vote_average": 5.0,
                "genre_ids": [],
            }
            for i in range(40)
        ]
        search_resp_data = {
            "page": 1,
            "total_pages": 1,
            "total_results": 40,
            "results": entries,
        }

        detail_fetch_ids: list[int] = []

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/3/search/" in url_str:
                return _make_json_response(search_resp_data)
            if "/3/movie/" in url_str:
                # Track which IDs got detail-fetched
                tmdb_id = int(url_str.split("/3/movie/")[1].split("?")[0])
                detail_fetch_ids.append(tmdb_id)
                return _make_json_response(
                    {"id": tmdb_id, "imdb_id": f"tt{tmdb_id}", "runtime": 120}
                )
            return _make_json_response({})

        p = cineby_mod.CinebyPlugin()
        p._client = mock_client
        p.base_url = "https://www.cineby.gd"
        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await p.search("movies")

        # Only first _MAX_DETAIL_FETCH entries should have detail fetched
        assert len(detail_fetch_ids) == max_detail
        assert set(detail_fetch_ids) == {1000 + i for i in range(max_detail)}

        # All 40 results should still be returned
        assert len(results) == 40

    @pytest.mark.asyncio
    async def test_results_without_detail_still_valid(self, cineby_mod, mock_client):
        """Results beyond detail cap have valid title/embed_url but empty imdb_id."""
        max_detail = cineby_mod._MAX_DETAIL_FETCH

        entries = [
            {
                "id": 2000 + i,
                "title": f"Film {i}",
                "media_type": "movie",
                "release_date": "2024-06-15",
                "overview": f"A film about {i}",
                "poster_path": "",
                "vote_average": 6.0,
                "genre_ids": [],
            }
            for i in range(max_detail + 5)
        ]
        search_resp_data = {
            "page": 1,
            "total_pages": 1,
            "total_results": len(entries),
            "results": entries,
        }

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "/3/search/" in url_str:
                return _make_json_response(search_resp_data)
            if "/3/movie/" in url_str:
                tmdb_id = int(url_str.split("/3/movie/")[1].split("?")[0])
                return _make_json_response(
                    {"id": tmdb_id, "imdb_id": f"tt{tmdb_id}", "runtime": 90}
                )
            return _make_json_response({})

        p = cineby_mod.CinebyPlugin()
        p._client = mock_client
        p.base_url = "https://www.cineby.gd"
        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await p.search("films")

        # Results with detail should have imdb_id
        for sr in results[:max_detail]:
            assert sr.metadata["imdb_id"] != "", f"Expected imdb_id for {sr.title}"
            assert sr.metadata["runtime"] == "90"

        # Results without detail should have empty imdb_id but valid title/embed
        for sr in results[max_detail:]:
            assert sr.metadata["imdb_id"] == ""
            assert sr.metadata["runtime"] == ""
            assert sr.title  # non-empty title
            assert "vidking.net/embed/movie/" in sr.download_link


# ---------------------------------------------------------------------------
# Detail fetch cap tests
# ---------------------------------------------------------------------------


class TestMaxConcurrent:
    """Tests for increased _max_concurrent setting."""

    def test_max_concurrent_is_eight(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        assert p._max_concurrent == 8


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, cineby_mod):
        p = cineby_mod.CinebyPlugin()

        await p.cleanup()  # Should not raise
