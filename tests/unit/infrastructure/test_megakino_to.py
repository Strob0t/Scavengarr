"""Tests for the megakino_to plugin (megakino.org / megakino.to)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "megakino_to.py"


@pytest.fixture()
def mod():
    """Import megakino_to plugin module."""
    spec = importlib.util.spec_from_file_location("megakino_to", _PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["megakino_to"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("megakino_to", None)


# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------

_BROWSE_RESPONSE = {
    "pager": {
        "totalItems": 1,
        "currentPage": 1,
        "pageSize": 20,
        "totalPages": 1,
    },
    "movies": [
        {
            "_id": "abc123",
            "title": "Iron Man",
            "year": 2008,
            "rating": 7.9,
            "genres": ["Action", "Sci-Fi"],
            "poster_path": "/poster.jpg",
            "tv": 0,
            "slug": "iron-man",
        }
    ],
}

_DETAIL_RESPONSE = {
    "_id": "abc123",
    "title": "Iron Man",
    "year": 2008,
    "rating": 7.9,
    "genres": ["Action", "Sci-Fi"],
    "tv": 0,
    "storyline": "A billionaire builds a suit.",
    "streams": [
        {
            "stream": "https://voe.sx/e/abc",
            "release": "Iron.Man.2008.German.DL",
            "source": "voe",
            "added": "2024-01-01",
        },
        {
            "stream": "https://dood.to/d/xyz",
            "release": "Iron.Man.2008.German.DL",
            "source": "doodstream",
            "added": "2024-01-01",
        },
    ],
    "tmdb": json.dumps(
        {
            "movie": [
                {
                    "movie_details": {
                        "imdb_id": "tt0371746",
                        "vote_average": 7.9,
                    }
                }
            ]
        }
    ),
}

_TV_BROWSE_RESPONSE = {
    "pager": {"totalItems": 1, "currentPage": 1, "pageSize": 20, "totalPages": 1},
    "movies": [
        {
            "_id": "tv001",
            "title": "Breaking Bad - Staffel 1",
            "year": 2008,
            "rating": 9.5,
            "genres": ["Drama", "Thriller"],
            "tv": 1,
            "slug": "breaking-bad-staffel-1",
        }
    ],
}

_TV_DETAIL_RESPONSE = {
    "_id": "tv001",
    "title": "Breaking Bad - Staffel 1",
    "year": 2008,
    "rating": 9.5,
    "genres": ["Drama", "Thriller"],
    "tv": 1,
    "s": 1,
    "streams": [
        {
            "stream": "https://voe.sx/e/ep1",
            "release": "Breaking.Bad.S01E01",
            "source": "voe",
            "e": 1,
        },
        {
            "stream": "https://voe.sx/e/ep2",
            "release": "Breaking.Bad.S01E02",
            "source": "voe",
            "e": 2,
        },
        {
            "stream": "https://dood.to/d/ep1",
            "release": "Breaking.Bad.S01E01",
            "source": "doodstream",
            "e": 1,
        },
    ],
    "tmdb": json.dumps({"movie": [{"movie_details": {"imdb_id": "tt0903747"}}]}),
}

_DETAIL_WITH_DELETED = {
    "_id": "del001",
    "title": "Test Movie",
    "year": 2024,
    "genres": ["Action"],
    "tv": 0,
    "streams": [
        {
            "stream": "https://voe.sx/e/good",
            "release": "Good.Release",
            "source": "voe",
        },
        {
            "stream": "https://dood.to/d/bad",
            "release": "Bad.Release",
            "source": "doodstream",
            "deleted": 1,
        },
    ],
}

_EMPTY_BROWSE = {
    "pager": {"totalItems": 0, "currentPage": 1, "pageSize": 20, "totalPages": 0},
    "movies": [],
}


def _make_plugin(
    mod,
    browse_resp: dict | None = None,
    detail_resp: dict | None = None,
):
    """Build a plugin backed by a fake transport."""
    br = json.dumps(browse_resp or _BROWSE_RESPONSE)
    dr = json.dumps(detail_resp or _DETAIL_RESPONSE)

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/data/browse/" in url:
            return httpx.Response(200, text=br)
        if "/data/watch/" in url:
            return httpx.Response(200, text=dr)
        return httpx.Response(200)

    transport = httpx.MockTransport(_handler)
    p = mod.MegakinoToPlugin()
    p._client = httpx.AsyncClient(transport=transport)
    p._domain_verified = True
    return p


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestDomainFromUrl:
    def test_extracts_domain(self, mod) -> None:
        assert mod._domain_from_url("https://voe.sx/e/abc") == "voe"

    def test_strips_www(self, mod) -> None:
        assert mod._domain_from_url("https://www.dood.to/d/xyz") == "dood"

    def test_invalid_url(self, mod) -> None:
        # urlparse("") → hostname=None → fallback host="" → parts=[""] → ""
        result = mod._domain_from_url("")
        assert isinstance(result, str)


class TestTypeForCategory:
    def test_none_returns_empty(self, mod) -> None:
        assert mod._type_for_category(None) == ""

    def test_movie_category(self, mod) -> None:
        assert mod._type_for_category(2000) == "movies"

    def test_tv_category(self, mod) -> None:
        assert mod._type_for_category(5000) == "tvseries"

    def test_unknown_category(self, mod) -> None:
        assert mod._type_for_category(9999) == ""


class TestDetectCategory:
    def test_movie(self, mod) -> None:
        assert mod._detect_category({"tv": 0}) == 2000

    def test_tv_series(self, mod) -> None:
        assert mod._detect_category({"tv": 1, "genres": ["Drama"]}) == 5000

    def test_animation(self, mod) -> None:
        assert mod._detect_category({"tv": 1, "genres": ["Animation"]}) == 5070

    def test_documentary(self, mod) -> None:
        assert mod._detect_category({"tv": 1, "genres": ["Dokumentation"]}) == 5080


class TestCollectStreams:
    def test_basic_collection(self, mod) -> None:
        streams = [
            {"stream": "https://voe.sx/e/a", "release": "R1", "source": "voe"},
            {"stream": "https://dood.to/d/b", "release": "R1", "source": "dood"},
        ]
        first, links = mod._collect_streams(streams)
        assert first == "https://voe.sx/e/a"
        assert len(links) == 2

    def test_skips_deleted(self, mod) -> None:
        streams = [
            {"stream": "https://voe.sx/e/a", "release": "R1"},
            {"stream": "https://dood.to/d/b", "release": "R2", "deleted": 1},
        ]
        first, links = mod._collect_streams(streams)
        assert len(links) == 1
        assert first == "https://voe.sx/e/a"

    def test_episode_filter(self, mod) -> None:
        streams = [
            {"stream": "https://voe.sx/e/ep1", "release": "S01E01", "e": 1},
            {"stream": "https://voe.sx/e/ep2", "release": "S01E02", "e": 2},
        ]
        first, links = mod._collect_streams(streams, episode=1)
        assert len(links) == 1
        assert "ep1" in first

    def test_empty_streams(self, mod) -> None:
        first, links = mod._collect_streams([])
        assert first == ""
        assert links == []

    def test_deduplication(self, mod) -> None:
        streams = [
            {"stream": "https://voe.sx/e/a", "release": "R1"},
            {"stream": "https://voe.sx/e/b", "release": "R1"},
        ]
        _, links = mod._collect_streams(streams)
        assert len(links) == 1


class TestExtractMetadata:
    def test_from_detail_json_string_tmdb(self, mod) -> None:
        """Real API returns tmdb as a JSON string with movie as a list."""
        detail = {
            "rating": 8.0,
            "runtime": "120",
            "genres": ["Action"],
            "storyline": "A story.",
            "tmdb": json.dumps(
                {"movie": [{"movie_details": {"imdb_id": "tt1234567"}}]}
            ),
        }
        meta = mod._extract_metadata(detail, {})
        assert meta["imdb_id"] == "tt1234567"
        assert meta["rating"] == "8.0"
        assert meta["description"] == "A story."

    def test_from_detail_dict_tmdb(self, mod) -> None:
        """Backwards-compat: tmdb as a dict with movie as a dict."""
        detail = {
            "rating": 8.0,
            "runtime": "120",
            "genres": ["Action"],
            "storyline": "A story.",
            "tmdb": {"movie": [{"movie_details": {"imdb_id": "tt1234567"}}]},
        }
        meta = mod._extract_metadata(detail, {})
        assert meta["imdb_id"] == "tt1234567"
        assert meta["rating"] == "8.0"
        assert meta["description"] == "A story."

    def test_fallback_to_browse(self, mod) -> None:
        meta = mod._extract_metadata(None, {"genres": ["Comedy"], "rating": 6.5})
        assert meta["genres"] == "Comedy"
        assert meta["rating"] == "6.5"
        assert meta["imdb_id"] == ""

    def test_truncates_long_description(self, mod) -> None:
        detail = {"storyline": "x" * 400, "genres": [], "tmdb": {}}
        meta = mod._extract_metadata(detail, {})
        assert len(meta["description"]) == 300
        assert meta["description"].endswith("...")


# ---------------------------------------------------------------------------
# Integration tests: plugin search
# ---------------------------------------------------------------------------


class TestMegakinoToSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, mod) -> None:
        p = _make_plugin(mod)
        results = await p.search("Iron Man")
        assert len(results) == 1
        assert results[0].title == "Iron Man (2008)"
        assert results[0].category == 2000
        assert len(results[0].download_links) == 2

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, mod) -> None:
        p = _make_plugin(mod)
        assert await p.search("") == []

    @pytest.mark.asyncio
    async def test_unsupported_category_returns_empty(self, mod) -> None:
        p = _make_plugin(mod)
        assert await p.search("Iron Man", category=9999) == []

    @pytest.mark.asyncio
    async def test_deleted_streams_filtered(self, mod) -> None:
        browse = {
            "pager": {
                "totalItems": 1,
                "currentPage": 1,
                "pageSize": 20,
                "totalPages": 1,
            },
            "movies": [
                {
                    "_id": "del001",
                    "title": "Test Movie",
                    "year": 2024,
                    "genres": ["Action"],
                    "tv": 0,
                }
            ],
        }
        p = _make_plugin(mod, browse_resp=browse, detail_resp=_DETAIL_WITH_DELETED)
        results = await p.search("Test")
        assert len(results) == 1
        assert len(results[0].download_links) == 1
        assert "voe" in results[0].download_links[0]["hoster"]

    @pytest.mark.asyncio
    async def test_episode_filtering(self, mod) -> None:
        p = _make_plugin(
            mod,
            browse_resp=_TV_BROWSE_RESPONSE,
            detail_resp=_TV_DETAIL_RESPONSE,
        )
        results = await p.search("Breaking Bad", season=1, episode=1)
        assert len(results) == 1
        # Only episode 1 streams (from 2 hosters: voe and doodstream)
        links = results[0].download_links
        assert len(links) == 2
        for link in links:
            assert "ep1" in link["link"]

    @pytest.mark.asyncio
    async def test_season_filter_excludes_wrong_season(self, mod) -> None:
        p = _make_plugin(
            mod,
            browse_resp=_TV_BROWSE_RESPONSE,
            detail_resp=_TV_DETAIL_RESPONSE,
        )
        # Detail has s=1, request season=2 -> no results
        results = await p.search("Breaking Bad", season=2)
        assert results == []

    @pytest.mark.asyncio
    async def test_imdb_id_in_metadata(self, mod) -> None:
        p = _make_plugin(mod)
        results = await p.search("Iron Man")
        assert results[0].metadata["imdb_id"] == "tt0371746"

    @pytest.mark.asyncio
    async def test_tv_category_detection(self, mod) -> None:
        p = _make_plugin(
            mod,
            browse_resp=_TV_BROWSE_RESPONSE,
            detail_resp=_TV_DETAIL_RESPONSE,
        )
        results = await p.search("Breaking Bad")
        assert len(results) == 1
        assert results[0].category == 5000

    @pytest.mark.asyncio
    async def test_empty_browse_returns_empty(self, mod) -> None:
        p = _make_plugin(mod, browse_resp=_EMPTY_BROWSE)
        assert await p.search("nonexistent") == []

    @pytest.mark.asyncio
    async def test_cleanup(self, mod) -> None:
        p = _make_plugin(mod)
        await p.search("test")
        assert p._client is not None
        await p.cleanup()
        assert p._client is None


class TestDomainFallback:
    @pytest.mark.asyncio
    async def test_fallback_to_second_domain(self, mod) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "megakino.org" in url and request.method == "HEAD":
                raise httpx.ConnectError("unreachable")
            if "megakino.to" in url and request.method == "HEAD":
                return httpx.Response(200)
            if "/data/browse/" in url:
                return httpx.Response(200, text=json.dumps(_BROWSE_RESPONSE))
            if "/data/watch/" in url:
                return httpx.Response(200, text=json.dumps(_DETAIL_RESPONSE))
            return httpx.Response(200)

        transport = httpx.MockTransport(_handler)
        p = mod.MegakinoToPlugin()
        p._client = httpx.AsyncClient(transport=transport)
        p._domain_verified = False

        results = await p.search("Iron Man")
        assert p.base_url == "https://megakino.to"
        assert len(results) == 1
