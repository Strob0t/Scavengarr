"""End-to-end tests for Stremio addon API endpoints.

Tests the full request-response cycle through:
    HTTP Request -> FastAPI Router -> Use Case -> JSON Response

Mocks are applied at the **port** level (PluginRegistryPort, TmdbClientPort,
SearchEnginePort, StreamLinkRepository, HosterResolverRegistry) so that real
use cases and router logic are exercised.

Endpoints covered:
    GET /api/v1/stremio/manifest.json
    GET /api/v1/stremio/catalog/{type}/{id}.json
    GET /api/v1/stremio/catalog/{type}/{id}/search={query}.json
    GET /api/v1/stremio/stream/{type}/{id}.json
    GET /api/v1/stremio/play/{stream_id}
    GET /api/v1/stremio/proxy/{stream_id}/{path}
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.application.stremio.answer import StreamAnswer
from scavengarr.application.stremio.stream_builder import FILE_NAME, HLS_MASTER
from scavengarr.application.use_cases.stremio_links import StremioLinks
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    ResolvedStream,
    StremioMetaPreview,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.anime_ids import NO_ANIME_IDS
from scavengarr.domain.ports.telemetry import NO_TELEMETRY
from scavengarr.infrastructure.anime.id_lists import LIST_URL, AnimeIdLists
from scavengarr.infrastructure.anime.kitsu_addon import ADDON_URL, KitsuAddonClient
from scavengarr.infrastructure.anime.resolver import KitsuAnimeIdResolver
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.hoster_resolvers import HosterResolverRegistry
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.episode_filter import filter_by_episode
from scavengarr.infrastructure.stremio.hls_proxy import FileAnswer
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match
from scavengarr.infrastructure.telemetry import Telemetry
from scavengarr.infrastructure.version import APP_VERSION
from scavengarr.interfaces.api.stremio.router import router

_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# Fake plugins
# ---------------------------------------------------------------------------


_KITSU_FIXTURES = Path(__file__).parents[1] / "fixtures" / "json" / "kitsu"


def _answer(streams: list[StremioStream]) -> StreamAnswer:
    """The use case's answer: a fresh, complete search."""
    return StreamAnswer(streams, "search", True, ())


def _kitsu_meta(name: str) -> dict[str, Any]:
    """One of the Anime Kitsu addon's meta answers, trimmed."""
    return json.loads((_KITSU_FIXTURES / name).read_text())


class _FakePythonPlugin:
    """Minimal Python plugin (has search(), no scraping)."""

    def __init__(
        self,
        name: str = "hdfilme",
        base_url: str = "https://hdfilme.legal",
        default_language: str = "de",
    ) -> None:
        self.name = name
        self.base_url = base_url
        self.provides = "stream"
        self.default_language = default_language
        self._results: list[SearchResult] = []
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        self.calls.append(
            {"query": query, "category": category, "season": season, "episode": episode}
        )
        return self._results


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_app(
    *,
    plugins: MagicMock | None = None,
    stremio_catalog_uc: Any = None,
    stremio_stream_uc: Any = None,
    stream_link_repo: AsyncMock | None = None,
    hoster_resolver_registry: Any = None,
    http_client: Any = None,
) -> FastAPI:
    """Build a minimal FastAPI app with the stremio router + mocked state."""
    app = FastAPI()
    app.include_router(router, prefix=_PREFIX)

    config = MagicMock()
    config.environment = "dev"
    config.app_name = "Scavengarr"

    app.state.config = config
    app.state.plugins = plugins or MagicMock()
    app.state.stremio_catalog_uc = stremio_catalog_uc
    app.state.stremio_stream_uc = stremio_stream_uc
    app.state.stream_link_repo = stream_link_repo
    app.state.hoster_resolver_registry = hoster_resolver_registry
    if stream_link_repo is not None:
        resolver = hoster_resolver_registry or AsyncMock()
        if isinstance(getattr(resolver, "bound_headers", None), AsyncMock):
            # bound_headers is synchronous: an AsyncMock attribute would
            # return a coroutine; these hosters bind no player headers
            resolver.bound_headers = MagicMock(return_value=())
        app.state.stremio_links = StremioLinks(repo=stream_link_repo, resolver=resolver)
    app.state.http_client = http_client or MagicMock()

    return app


def _make_meta(
    *,
    id: str = "tt1234567",
    type: str = "movie",
    name: str = "Test Movie",
    poster: str = "https://image.tmdb.org/poster.jpg",
    description: str = "A great movie",
    release_info: str = "2024",
    imdb_rating: str = "7.5",
    genres: list[str] | None = None,
) -> StremioMetaPreview:
    """Convenience factory for StremioMetaPreview."""
    return StremioMetaPreview(
        id=id,
        type=type,
        name=name,
        poster=poster,
        description=description,
        release_info=release_info,
        imdb_rating=imdb_rating,
        genres=genres or ["Action", "Drama"],
    )


def _make_search_result(
    title: str = "Test.Movie.2024.1080p",
    download_link: str = "https://voe.sx/e/abc123",
    **kwargs: Any,
) -> SearchResult:
    """Convenience factory for SearchResult."""
    defaults: dict[str, Any] = {
        "title": title,
        "download_link": download_link,
        "size": "1.5 GB",
        "source_url": "https://example.com/detail/1",
        "category": 2000,
    }
    defaults.update(kwargs)
    return SearchResult(**defaults)


# ---------------------------------------------------------------------------
# Manifest endpoint
# ---------------------------------------------------------------------------


class TestManifestEndpoint:
    """GET /api/v1/stremio/manifest.json"""

    def test_returns_valid_manifest(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = ["hdfilme", "kinoger"]

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/manifest.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "community.scavengarr"
        assert data["version"] == APP_VERSION
        assert data["name"] == "Scavengarr"

    def test_manifest_has_types(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/manifest.json")
        data = resp.json()

        assert "movie" in data["types"]
        assert "series" in data["types"]

    def test_manifest_has_catalogs(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        app = _make_app(plugins=plugins, stremio_catalog_uc=AsyncMock())
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/manifest.json")
        data = resp.json()

        assert len(data["catalogs"]) == 2
        catalog_types = [c["type"] for c in data["catalogs"]]
        assert "movie" in catalog_types
        assert "series" in catalog_types

    def test_manifest_has_resources(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        app = _make_app(plugins=plugins, stremio_catalog_uc=AsyncMock())
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/manifest.json")
        data = resp.json()

        assert "catalog" in data["resources"]
        assert "stream" in data["resources"]

    def test_manifest_has_id_prefixes(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/manifest.json")
        data = resp.json()

        assert "tt" in data["idPrefixes"]
        assert "tmdb:" in data["idPrefixes"]
        assert "kitsu:" in data["idPrefixes"]

    def test_manifest_cors_headers(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/manifest.json")

        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Catalog endpoint (trending)
# ---------------------------------------------------------------------------


class TestCatalogEndpoint:
    """GET /api/v1/stremio/catalog/{type}/{id}.json"""

    def test_trending_movies(self) -> None:
        meta = _make_meta(id="tt1234567", type="movie", name="Iron Man")

        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(return_value=[meta])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies.json"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["metas"]) == 1
        assert data["metas"][0]["name"] == "Iron Man"
        assert data["metas"][0]["id"] == "tt1234567"
        assert data["metas"][0]["type"] == "movie"

    def test_trending_series(self) -> None:
        meta = _make_meta(id="tt9999999", type="series", name="Breaking Bad")

        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(return_value=[meta])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/series/scavengarr-trending-series.json"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["metas"]) == 1
        assert data["metas"][0]["name"] == "Breaking Bad"

    def test_invalid_content_type_returns_empty(self) -> None:
        catalog_uc = AsyncMock()

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/anime/scavengarr-trending-anime.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_no_catalog_uc_returns_empty(self) -> None:
        """When stremio_catalog_uc is None (no TMDB key), return empty list."""
        app = _make_app(stremio_catalog_uc=None)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_catalog_meta_fields(self) -> None:
        meta = _make_meta(
            id="tt5555555",
            type="movie",
            name="Interstellar",
            poster="https://image.tmdb.org/poster_interstellar.jpg",
            description="A space epic",
            release_info="2014",
            imdb_rating="8.7",
            genres=["Sci-Fi", "Drama"],
        )

        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(return_value=[meta])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies.json"
        )

        m = resp.json()["metas"][0]
        assert m["id"] == "tt5555555"
        assert m["name"] == "Interstellar"
        assert m["poster"] == "https://image.tmdb.org/poster_interstellar.jpg"
        assert m["description"] == "A space epic"
        assert m["releaseInfo"] == "2014"
        assert m["imdbRating"] == "8.7"
        assert m["genres"] == ["Sci-Fi", "Drama"]

    def test_multiple_metas(self) -> None:
        metas = [_make_meta(id=f"tt{i}", name=f"Movie {i}") for i in range(5)]

        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(return_value=metas)

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies.json"
        )

        assert len(resp.json()["metas"]) == 5

    def test_catalog_cors_headers(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(return_value=[])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies.json"
        )

        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Catalog search endpoint
# ---------------------------------------------------------------------------


class TestCatalogSearchEndpoint:
    """GET /api/v1/stremio/catalog/{type}/{id}/search={query}.json"""

    def test_search_movies(self) -> None:
        meta = _make_meta(id="tt0371746", name="Iron Man")

        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(return_value=[meta])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies"
            "/search=iron man.json"
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["metas"]) == 1
        assert data["metas"][0]["name"] == "Iron Man"

    def test_search_series(self) -> None:
        meta = _make_meta(id="tt0903747", type="series", name="Breaking Bad")

        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(return_value=[meta])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/series/scavengarr-trending-series"
            "/search=breaking bad.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"][0]["name"] == "Breaking Bad"

    def test_search_invalid_type_returns_empty(self) -> None:
        catalog_uc = AsyncMock()

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/catalog/anime/some-id/search=naruto.json")

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_search_no_uc_returns_empty(self) -> None:
        app = _make_app(stremio_catalog_uc=None)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies"
            "/search=test.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_search_empty_results(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(return_value=[])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies"
            "/search=nonexistent.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_search_cors_headers(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(return_value=[])

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies"
            "/search=test.json"
        )

        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Stream endpoint
# ---------------------------------------------------------------------------


class TestStreamEndpoint:
    """GET /api/v1/stremio/stream/{type}/{id}.json"""

    def test_movie_stream_by_imdb_id(self) -> None:
        stream = StremioStream(
            name="Iron Man (2008) 1080p",
            description="hdfilme | German Dub | VOE | 1.5 GB",
            url="https://voe.sx/e/abc123",
        )
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([stream]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["streams"]) == 1
        assert data["streams"][0]["name"] == "Iron Man (2008) 1080p"
        assert data["streams"][0]["url"] == "https://voe.sx/e/abc123"

    def test_series_stream_with_season_episode(self) -> None:
        stream = StremioStream(
            name="Breaking Bad S01E05",
            description="kinoger | German Dub",
            url="https://voe.sx/e/episode5",
        )
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([stream]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt0903747:1:5.json")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["streams"]) == 1

        # Verify the use case received correct parsed request
        call_args = stream_uc.answer.call_args
        request: StremioStreamRequest = call_args[0][0]
        assert request.imdb_id == "tt0903747"
        assert request.content_type == "series"
        assert request.season == 1
        assert request.episode == 5

    def test_tmdb_id_movie(self) -> None:
        stream = StremioStream(
            name="Test Movie", description="plugin", url="https://example.com/v"
        )
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([stream]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tmdb:12345.json")

        assert resp.status_code == 200
        call_args = stream_uc.answer.call_args
        request: StremioStreamRequest = call_args[0][0]
        assert request.imdb_id == "tmdb:12345"
        assert request.content_type == "movie"

    def test_tmdb_id_series_with_season_episode(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tmdb:67890:2:10.json")

        assert resp.status_code == 200
        call_args = stream_uc.answer.call_args
        request: StremioStreamRequest = call_args[0][0]
        assert request.imdb_id == "tmdb:67890"
        assert request.season == 2
        assert request.episode == 10

    def test_invalid_content_type_returns_empty(self) -> None:
        stream_uc = AsyncMock()

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/anime/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []
        # Use case should NOT have been called
        stream_uc.answer.assert_not_awaited()

    def test_invalid_id_format_returns_empty(self) -> None:
        stream_uc = AsyncMock()

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/notanid.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []
        stream_uc.answer.assert_not_awaited()

    def test_no_stream_uc_returns_empty(self) -> None:
        """When stremio_stream_uc is None, return empty streams."""
        app = _make_app(stremio_stream_uc=None)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_empty_streams(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0000001.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_multiple_streams(self) -> None:
        streams = [
            StremioStream(
                name=f"Stream {i}",
                description=f"plugin{i}",
                url=f"https://example.com/v{i}",
            )
            for i in range(4)
        ]
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer(streams))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert len(resp.json()["streams"]) == 4

    def test_stream_response_fields(self) -> None:
        stream = StremioStream(
            name="Iron Man (2008) 1080p",
            description="hdfilme | German Dub | VOE | 2.5 GB",
            url="https://voe.sx/e/xyz",
        )
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([stream]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        s = resp.json()["streams"][0]
        assert s["name"] == "Iron Man (2008) 1080p"
        assert s["description"] == "hdfilme | German Dub | VOE | 2.5 GB"
        assert s["url"] == "https://voe.sx/e/xyz"

    def test_stream_cors_headers(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_series_without_season_episode_parsed_as_movie_style(self) -> None:
        """Series ID without :S:E still creates a request (no season/episode)."""
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt0903747.json")

        assert resp.status_code == 200
        call_args = stream_uc.answer.call_args
        request: StremioStreamRequest = call_args[0][0]
        assert request.imdb_id == "tt0903747"
        assert request.content_type == "series"
        assert request.season is None
        assert request.episode is None


# ---------------------------------------------------------------------------
# Stream ID parsing (edge cases tested via the endpoint)
# ---------------------------------------------------------------------------


class TestStreamIdParsing:
    """Verify _parse_stream_id edge cases through the endpoint."""

    def _get_streams(self, content_type: str, stream_id: str) -> dict:
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(return_value=_answer([]))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/{content_type}/{stream_id}.json")
        return resp.json()

    def test_imdb_id_movie(self) -> None:
        data = self._get_streams("movie", "tt0371746")
        assert data == {"streams": []}

    def test_imdb_id_series_with_se(self) -> None:
        data = self._get_streams("series", "tt0903747:3:12")
        assert data == {"streams": []}

    def test_tmdb_id_movie(self) -> None:
        data = self._get_streams("movie", "tmdb:550")
        assert data == {"streams": []}

    def test_tmdb_id_series_with_se(self) -> None:
        data = self._get_streams("series", "tmdb:1399:1:1")
        assert data == {"streams": []}

    def test_invalid_id_prefix(self) -> None:
        data = self._get_streams("movie", "imdb:123")
        assert data == {"streams": []}

    def test_series_non_numeric_season(self) -> None:
        data = self._get_streams("series", "tt1234567:abc:1")
        assert data == {"streams": []}

    def test_series_non_numeric_episode(self) -> None:
        data = self._get_streams("series", "tt1234567:1:abc")
        assert data == {"streams": []}

    def test_tmdb_series_non_numeric_season(self) -> None:
        data = self._get_streams("series", "tmdb:123:abc:1")
        assert data == {"streams": []}


# ---------------------------------------------------------------------------
# Play endpoint
# ---------------------------------------------------------------------------


class TestPlayEndpoint:
    """GET /api/v1/stremio/play/{stream_id}"""

    def test_play_redirects_to_video_url(self) -> None:
        link = CachedStreamLink(
            stream_id="abc123",
            hoster_url="https://voe.sx/e/abc123",
            title="Iron Man",
            hoster="voe",
        )
        resolved = ResolvedStream(
            video_url="https://delivery.voe.sx/video.mp4",
            is_hls=False,
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=resolved)

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app, follow_redirects=False)

        resp = client.get(f"{_PREFIX}/stremio/play/abc123")

        assert resp.status_code == 302
        assert resp.headers["location"] == "https://delivery.voe.sx/video.mp4"

    def test_play_stream_not_found(self) -> None:
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)

        registry = AsyncMock()

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/play/nonexistent")

        assert resp.status_code == 404
        data = resp.json()
        assert "expired" in data["error"] or "not found" in data["error"]

    def test_play_resolution_failed(self) -> None:
        link = CachedStreamLink(
            stream_id="abc123",
            hoster_url="https://voe.sx/e/dead",
            title="Dead Link",
            hoster="voe",
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=None)

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/play/abc123")

        assert resp.status_code == 502
        data = resp.json()
        assert "video URL" in data["error"] or "extract" in data["error"]

    def test_play_no_repo_configured(self) -> None:
        """When stream_link_repo is None, return 503."""
        app = _make_app(stream_link_repo=None, hoster_resolver_registry=AsyncMock())
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/play/abc123")

        assert resp.status_code == 503
        assert "not configured" in resp.json()["error"]

    def test_play_cors_headers_on_redirect(self) -> None:
        link = CachedStreamLink(
            stream_id="abc123",
            hoster_url="https://voe.sx/e/abc123",
            title="Test",
            hoster="voe",
        )
        resolved = ResolvedStream(
            video_url="https://delivery.voe.sx/video.mp4",
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=resolved)

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app, follow_redirects=False)

        resp = client.get(f"{_PREFIX}/stremio/play/abc123")

        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_play_cors_headers_on_error(self) -> None:
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)

        registry = AsyncMock()

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/play/xyz")

        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_play_hls_stream(self) -> None:
        link = CachedStreamLink(
            stream_id="hls123",
            hoster_url="https://filemoon.sx/e/hls123",
            title="HLS Test",
            hoster="filemoon",
        )
        resolved = ResolvedStream(
            video_url="https://cdn.filemoon.sx/master.m3u8",
            is_hls=True,
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=resolved)

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app, follow_redirects=False)

        resp = client.get(f"{_PREFIX}/stremio/play/hls123")

        assert resp.status_code == 302
        assert resp.headers["location"] == "https://cdn.filemoon.sx/master.m3u8"

    def test_play_resolves_for_a_player_whose_headers_the_cdn_binds(self) -> None:
        """VEEV: Stremio's streaming server passes Firefox's Accept-Language
        on to the CDN, which binds the URL to it (production, 2026-10-06)."""
        link = CachedStreamLink(
            stream_id="veev1",
            hoster_url="https://veev.to/e/abc",
            hoster="veev",
            video_url="https://cdn.veev.example/stored.mp4",
            video_headers=json.dumps({"User-Agent": "UA"}),
            resolved_at=time.time(),
        )
        repo = AsyncMock()
        repo.get = AsyncMock(side_effect=lambda sid: link if sid == "veev1" else None)
        registry = AsyncMock()
        registry.bound_headers = MagicMock(
            return_value=("user-agent", "accept-language")
        )
        registry.resolve_for_client = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.veev.example/own.mp4")
        )
        client = TestClient(
            _make_app(stream_link_repo=repo, hoster_resolver_registry=registry),
            follow_redirects=False,
        )

        browser = client.get(
            f"{_PREFIX}/stremio/play/veev1",
            headers={"User-Agent": "UA", "Accept-Language": "de"},
        )
        probe = client.get(
            f"{_PREFIX}/stremio/play/veev1", headers={"User-Agent": "UA"}
        )

        assert browser.headers["location"] == "https://cdn.veev.example/own.mp4"
        assert probe.headers["location"] == "https://cdn.veev.example/stored.mp4"
        registry.resolve_for_client.assert_awaited_once_with(
            "https://veev.to/e/abc",
            "veev",
            {"user-agent": "UA", "accept-language": "de"},
        )

    def test_play_resolver_receives_correct_hoster(self) -> None:
        link = CachedStreamLink(
            stream_id="test1",
            hoster_url="https://streamtape.com/v/abc",
            title="Test",
            hoster="streamtape",
        )
        resolved = ResolvedStream(video_url="https://cdn.streamtape.com/video.mp4")

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=resolved)

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)
        client = TestClient(app, follow_redirects=False)

        client.get(f"{_PREFIX}/stremio/play/test1")

        registry.resolve.assert_awaited_once_with(
            "https://streamtape.com/v/abc", "streamtape", refresh=False
        )


# ---------------------------------------------------------------------------
# Full stream resolution flow (router -> use case -> response)
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Verify that use case errors are caught and return empty responses."""

    def test_catalog_error_returns_empty_metas(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(side_effect=RuntimeError("TMDB down"))

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_catalog_search_error_returns_empty_metas(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(side_effect=RuntimeError("TMDB down"))

        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/catalog/movie/scavengarr-trending-movies"
            "/search=test.json"
        )

        assert resp.status_code == 200
        assert resp.json()["metas"] == []

    def test_stream_error_returns_empty_streams(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(side_effect=RuntimeError("plugin timeout"))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_stream_error_has_cors_headers(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.answer = AsyncMock(side_effect=RuntimeError("boom"))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /api/v1/stremio/health"""

    def test_healthy_when_all_configured(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = ["hdfilme", "aniworld"]

        resolver = MagicMock(spec=HosterResolverRegistry)
        resolver.supported_hosters = ["voe", "streamtape"]

        app = _make_app(
            plugins=plugins,
            stremio_catalog_uc=AsyncMock(),
            stremio_stream_uc=AsyncMock(),
            stream_link_repo=AsyncMock(),
            hoster_resolver_registry=resolver,
        )
        app.state.tmdb_client = MagicMock()
        app.state.anime_ids = MagicMock()

        client = TestClient(app)
        resp = client.get(f"{_PREFIX}/stremio/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert data["tmdb_configured"] is True
        assert data["anime_ids_configured"] is True
        assert data["stream_plugin_count"] == 2
        assert data["stream_plugins"] == ["hdfilme", "aniworld"]
        assert data["stream_uc_initialized"] is True
        assert data["catalog_uc_initialized"] is True
        assert data["hoster_resolver_configured"] is True
        assert data["supported_hosters"] == ["voe", "streamtape"]
        assert data["stream_link_repo_configured"] is True

    def test_reports_version_commit_and_build_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCAVENGARR_COMMIT", "7eeb50b63a57")
        monkeypatch.setenv("SCAVENGARR_BUILT", "2026-10-06T18:00:00Z")
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        data = (
            TestClient(_make_app(plugins=plugins))
            .get(f"{_PREFIX}/stremio/health")
            .json()
        )

        assert (data["version"], data["commit"], data["built"]) == (
            APP_VERSION,
            "7eeb50b63a57",
            "2026-10-06T18:00:00Z",
        )

    def test_metrics_are_the_telemetry_statistics(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []
        app = _make_app(plugins=plugins)
        app.state.telemetry = Telemetry()

        data = TestClient(app).get(f"{_PREFIX}/stremio/health").json()

        assert set(data["metrics"]) == {"uptime_seconds", "plugins", "event_loop"}

    def test_unhealthy_no_tmdb(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = ["hdfilme"]

        app = _make_app(
            plugins=plugins,
            stremio_stream_uc=None,
            stremio_catalog_uc=None,
        )

        client = TestClient(app)
        resp = client.get(f"{_PREFIX}/stremio/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["healthy"] is False
        assert data["tmdb_configured"] is False

    def test_unhealthy_no_plugins(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        resolver = MagicMock()
        resolver.list_hosters.return_value = ["voe"]

        app = _make_app(
            plugins=plugins,
            stremio_catalog_uc=AsyncMock(),
            stremio_stream_uc=AsyncMock(),
            stream_link_repo=AsyncMock(),
            hoster_resolver_registry=resolver,
        )
        app.state.tmdb_client = MagicMock()

        client = TestClient(app)
        resp = client.get(f"{_PREFIX}/stremio/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["healthy"] is False
        assert data["stream_plugin_count"] == 0

    def test_unhealthy_no_stream_link_repo(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = ["hdfilme"]

        resolver = MagicMock()
        resolver.list_hosters.return_value = ["voe"]

        app = _make_app(
            plugins=plugins,
            stremio_catalog_uc=AsyncMock(),
            stremio_stream_uc=AsyncMock(),
            stream_link_repo=None,
            hoster_resolver_registry=resolver,
        )
        app.state.tmdb_client = MagicMock()

        client = TestClient(app)
        resp = client.get(f"{_PREFIX}/stremio/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["healthy"] is False
        assert data["stream_link_repo_configured"] is False

    def test_health_plugin_error_handled(self) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.side_effect = RuntimeError("registry broken")

        app = _make_app(plugins=plugins)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/health")

        assert resp.status_code == 503
        data = resp.json()
        assert data["stream_plugin_count"] == 0


class TestStreamFullFlow:
    """Test the stream endpoint with a real StremioStreamUseCase.

    Mocks: TmdbClientPort, PluginRegistryPort, SearchEnginePort, StreamLinkRepository.
    Real: StremioStreamUseCase, StreamSorter, stream_converter, title_matcher.
    """

    def _make_full_flow_app(
        self,
        *,
        title_info: TitleMatchInfo | None = None,
        plugin_names: list[str] | None = None,
        plugin: _FakePythonPlugin | None = None,
        search_results: list[SearchResult] | None = None,
        telemetry: Telemetry | None = None,
        anime_ids: Any = None,
    ) -> FastAPI:
        """Build app with a real StremioStreamUseCase."""
        from scavengarr.application.use_cases.stremio_stream import (
            StremioStreamUseCase,
        )

        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=title_info)
        tmdb.get_title_by_tmdb_id = AsyncMock(
            return_value=title_info.title if title_info else None
        )

        names = plugin_names or (["hdfilme"] if plugin else [])
        p = plugin or _FakePythonPlugin()

        plugins = MagicMock()
        plugins.get_by_provides.return_value = names
        plugins.get.return_value = p
        plugins.get_languages.return_value = ["de"]
        plugins.get_mode.return_value = "httpx"

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=search_results or [])

        stream_link_repo = AsyncMock()

        config = StremioConfig()

        stream_uc = StremioStreamUseCase(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            config=config,
            sorter=StreamSorter(config),
            convert_fn=convert_search_results,
            filter_fn=filter_by_title_match,
            episode_filter_fn=filter_by_episode,
            user_agent=DEFAULT_USER_AGENT,
            max_results_var=search_max_results,
            stream_link_repo=stream_link_repo,
            pool=ConcurrencyPool(),
            telemetry=telemetry or NO_TELEMETRY,
            anime_ids=anime_ids or NO_ANIME_IDS,
        )

        app = FastAPI()
        app.include_router(router, prefix=_PREFIX)

        app_config = MagicMock()
        app_config.environment = "dev"
        app_config.app_name = "Scavengarr"

        app.state.config = app_config
        app.state.plugins = plugins
        app.state.stremio_catalog_uc = None
        app.state.stremio_stream_uc = stream_uc
        app.state.stream_link_repo = stream_link_repo
        app.state.hoster_resolver_registry = None

        return app

    def test_title_not_found_returns_empty(self) -> None:
        app = self._make_full_flow_app(title_info=None)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0000001.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_no_plugins_returns_empty(self) -> None:
        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Iron Man", year=2008),
            plugin_names=[],
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_no_search_results_returns_empty(self) -> None:
        plugin = _FakePythonPlugin()
        plugin._results = []

        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Iron Man", year=2008),
            plugin=plugin,
            search_results=[],
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_matching_result_produces_streams(self) -> None:
        plugin = _FakePythonPlugin(name="hdfilme")
        result = _make_search_result(
            title="Iron Man",
            download_link="https://voe.sx/e/ironman",
            download_links=[
                {
                    "hoster": "voe",
                    "link": "https://voe.sx/e/ironman",
                    "language": "German Dub",
                },
            ],
        )
        plugin._results = [result]

        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Iron Man", year=2008),
            plugin=plugin,
            search_results=[result],
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        data = resp.json()
        assert len(data["streams"]) >= 1
        # Stream URLs should be proxy play URLs (stream link repo is set)
        for s in data["streams"]:
            assert "stremio/play/" in s["url"]

    def test_stream_has_proxy_play_url(self) -> None:
        plugin = _FakePythonPlugin(name="hdfilme")
        result = _make_search_result(
            title="Iron Man",
            download_link="https://voe.sx/e/ironman",
        )
        plugin._results = [result]

        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Iron Man", year=2008),
            plugin=plugin,
            search_results=[result],
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        if streams:
            # Every URL should be a proxy play URL
            for s in streams:
                assert "/api/v1/stremio/play/" in s["url"]

    # kitsu: ids (the Anime Kitsu addon's catalogs) are translated with the
    # addon's meta before the search; the public id list is the fallback

    @staticmethod
    def _anime_ids() -> KitsuAnimeIdResolver:
        cache = AsyncMock()
        cache.get.return_value = None
        http = httpx.AsyncClient()
        return KitsuAnimeIdResolver(
            addon=KitsuAddonClient(http_client=http, cache=cache),
            lists=AnimeIdLists(http_client=http, cache=cache),
        )

    @respx.mock
    def test_a_kitsu_episode_is_searched_as_imdb_counts_it(self) -> None:
        respx.get(f"{ADDON_URL}/meta/series/kitsu:41982.json").respond(
            json=_kitsu_meta("meta_series_41982.json")
        )
        plugin = _FakePythonPlugin(name="aniworld")
        result = _make_search_result(
            title="Haikyu S03E15",
            download_link="https://voe.sx/e/haikyu",
            category=5000,
            download_links=[
                {
                    "hoster": "voe",
                    "link": "https://voe.sx/e/haikyu",
                    "language": "German Dub",
                },
            ],
        )
        plugin._results = [result]
        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Haikyu", year=2014),
            plugin=plugin,
            search_results=[result],
            anime_ids=self._anime_ids(),
        )

        with structlog.testing.capture_logs() as logs:
            resp = TestClient(app).get(
                f"{_PREFIX}/stremio/stream/series/kitsu:41982:3.json"
            )

        assert resp.status_code == 200
        assert len(resp.json()["streams"]) >= 1
        assert plugin.calls == [
            {"query": "Haikyu", "category": 5000, "season": 3, "episode": 15}
        ]
        translated = [e for e in logs if e["event"] == "anime_id_translated"]
        assert translated[0]["source"] == "addon"

    @respx.mock
    def test_a_kitsu_movie_is_searched_as_a_movie(self) -> None:
        respx.get(f"{ADDON_URL}/meta/movie/kitsu:11614.json").respond(
            json=_kitsu_meta("meta_movie_11614.json")
        )
        plugin = _FakePythonPlugin(name="aniworld")
        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Your Name", year=2016),
            plugin=plugin,
            anime_ids=self._anime_ids(),
        )

        resp = TestClient(app).get(f"{_PREFIX}/stremio/stream/movie/kitsu:11614.json")

        assert resp.json() == {"streams": []}
        assert plugin.calls == [
            {"query": "Your Name", "category": 2000, "season": None, "episode": None}
        ]

    @respx.mock
    def test_an_unknown_kitsu_id_answers_nothing(self) -> None:
        respx.get(f"{ADDON_URL}/meta/series/kitsu:99999999.json").respond(404)
        respx.get(LIST_URL).respond(500)
        plugin = _FakePythonPlugin(name="aniworld")
        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Haikyu", year=2014),
            plugin=plugin,
            anime_ids=self._anime_ids(),
        )

        with structlog.testing.capture_logs() as logs:
            resp = TestClient(app).get(
                f"{_PREFIX}/stremio/stream/series/kitsu:99999999:1.json"
            )

        assert resp.json() == {"streams": []}
        assert plugin.calls == []
        events = [e["event"] for e in logs]
        assert "anime_id_lists_failed" in events
        assert "anime_id_lookup_failed" in events

    def test_the_request_is_in_the_metrics(self) -> None:
        plugin = _FakePythonPlugin(name="hdfilme")
        result = _make_search_result(
            title="Iron Man", download_link="https://voe.sx/e/ironman"
        )
        plugin._results = [result]
        telemetry = Telemetry()
        app = self._make_full_flow_app(
            title_info=TitleMatchInfo(title="Iron Man", year=2008),
            plugin=plugin,
            search_results=[result],
            telemetry=telemetry,
        )

        TestClient(app).get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        text = telemetry.render().decode()
        request = 'scavengarr_stremio_request_total{outcome="streams",source="search"}'
        assert f"{request} 1.0" in text
        search = 'scavengarr_plugin_search_total{outcome="hits",plugin="hdfilme"}'
        assert f"{search} 1.0" in text
        assert "tt0371746" not in text


# ---------------------------------------------------------------------------
# HLS Proxy endpoint
# ---------------------------------------------------------------------------

_PROXY_MODULE = "scavengarr.interfaces.api.stremio.router"


def _make_hls_link(
    *,
    stream_id: str = "hls-abc",
    video_url: str = "https://cdn.dropcdn.io/hls2/01/video/master.m3u8?t=abc&expires=123",
    video_headers: dict[str, str] | None = None,
) -> CachedStreamLink:
    headers = video_headers or {"Referer": "https://dropload.io/"}
    return CachedStreamLink(
        stream_id=stream_id,
        hoster_url="https://dropload.io/e/xyz",
        title="Test HLS",
        hoster="dropload",
        video_url=video_url,
        video_headers=json.dumps(headers),
        is_hls=True,
    )


class TestProxyHlsEndpoint:
    """GET /api/v1/stremio/proxy/{stream_id}/{path}"""

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_manifest_fetch_and_rewrite(self, mock_fetch: AsyncMock) -> None:
        link = _make_hls_link()
        manifest = (
            b"#EXTM3U\n"
            b"#EXT-X-TARGETDURATION:10\n"
            b"#EXTINF:10.0,\n"
            b"https://cdn.dropcdn.io/hls2/01/video/seg-1.ts?t=abc\n"
            b"#EXT-X-ENDLIST\n"
        )
        mock_fetch.return_value = (manifest, "application/vnd.apple.mpegurl")

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?t=abc&expires=123"
        )

        assert resp.status_code == 200
        assert "mpegurl" in resp.headers["content-type"]
        body = resp.text
        # CDN URLs should be rewritten to proxy URLs, at a copy of the link
        assert "cdn.dropcdn.io" not in body
        assert "/api/v1/stremio/proxy/hls-abc." in body
        assert "/seg-1.ts" in body
        assert resp.headers.get("access-control-allow-origin") == "*"

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_head_on_manifest(self, mock_fetch: AsyncMock) -> None:
        """Stremio Web asks for the content type with HEAD before it plays
        (stremio-video's getContentType); a 405 without CORS headers ended
        every proxied stream in "Video is not supported"."""
        mock_fetch.return_value = (
            b"#EXTM3U\n#EXTINF:10.0,\nseg-1.ts\n#EXT-X-ENDLIST\n",
            "application/vnd.apple.mpegurl",
        )
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=_make_hls_link())
        client = TestClient(_make_app(stream_link_repo=repo))

        resp = client.head(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?t=abc")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/vnd.apple.mpegurl"
        assert resp.headers.get("access-control-allow-origin") == "*"

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_variant_from_the_cdn_root(self, mock_fetch: AsyncMock) -> None:
        """Vidsonic lists its variant from the CDN root; the player follows
        the rewritten URL and the proxy fetches it from that root."""
        master = (
            b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1646000\n"
            b"/secure/98/r00/video.m3u8?expires=1&md5=ab\n"
        )
        variant = b"#EXTM3U\n#EXTINF:6,\nseg-1.ts\n#EXT-X-ENDLIST\n"
        mock_fetch.side_effect = [
            (master, "application/vnd.apple.mpegurl"),
            (variant, "application/vnd.apple.mpegurl"),
        ]
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=_make_hls_link())
        client = TestClient(_make_app(stream_link_repo=repo))

        body = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?t=abc").text
        variant_url = body.splitlines()[-1]
        assert "/api/v1/stremio/proxy/hls-abc." in variant_url
        assert variant_url.endswith("//secure/98/r00/video.m3u8?expires=1&md5=ab")
        resp = client.get(variant_url)

        assert resp.status_code == 200
        assert mock_fetch.await_args_list[1].args[1] == (
            "https://cdn.dropcdn.io/secure/98/r00/video.m3u8?expires=1&md5=ab"
        )

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    def test_segment_streaming(self, mock_stream: AsyncMock) -> None:
        link = _make_hls_link()
        segment_data = b"\x00\x01\x02segment-bytes"

        async def _fake_iter() -> Any:
            yield segment_data

        mock_stream.return_value = (_fake_iter(), "video/mp2t")

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/seg-1.ts?t=abc&expires=123")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp2t"
        assert resp.content == segment_data

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    @pytest.mark.parametrize(
        "path", ["http://127.0.0.1:8080/admin", "https://evil.example/x.ts"]
    )
    def test_path_outside_cdn_rejected(self, mock_stream: AsyncMock, path: str) -> None:
        """No request to a host other than the stream's CDN (SSRF)."""
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=_make_hls_link())
        client = TestClient(_make_app(stream_link_repo=repo))

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/{path}")

        assert resp.status_code == 400
        mock_stream.assert_not_awaited()

    def test_proxy_not_found(self) -> None:
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=None)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/nonexistent/master.m3u8")

        assert resp.status_code == 404
        assert "expired" in resp.json()["error"] or "not found" in resp.json()["error"]

    def test_proxy_not_hls(self) -> None:
        link = CachedStreamLink(
            stream_id="not-hls",
            hoster_url="https://voe.sx/e/abc",
            title="Not HLS",
            hoster="voe",
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/not-hls/master.m3u8")

        assert resp.status_code == 400
        assert "not an HLS" in resp.json()["error"]

    def test_proxy_no_repo(self) -> None:
        app = _make_app(stream_link_repo=None)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8")

        assert resp.status_code == 503
        assert "not configured" in resp.json()["error"]

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_cdn_http_error_returns_502(self, mock_fetch: AsyncMock) -> None:
        link = _make_hls_link()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_fetch.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=MagicMock(), response=mock_resp
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?t=abc")

        assert resp.status_code == 502
        assert "CDN" in resp.json()["error"]

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    def test_segment_cdn_error_returns_502(self, mock_stream: AsyncMock) -> None:
        link = _make_hls_link()
        mock_stream.side_effect = httpx.ConnectError("connection refused")

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/seg-1.ts?t=abc")

        assert resp.status_code == 502
        assert "CDN" in resp.json()["error"]

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    def test_a_cdn_error_logs_no_url(self, mock_stream: AsyncMock) -> None:
        """CDN URLs carry tokens and the client's address (``i=``) in path
        and query (code review, 2026-10-06): the log names the CDN only."""
        mock_stream.side_effect = _refused("https://cdn.dropcdn.io/x")
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=_make_hls_link())
        client = TestClient(_make_app(stream_link_repo=repo))

        with structlog.testing.capture_logs() as logs:
            client.get(f"{_PREFIX}/stremio/proxy/hls-abc/seg-1.ts?t=secret&i=1.2.3.4")

        errors = [e for e in logs if e["event"] == "hls_proxy_cdn_error"]
        assert errors and errors[0]["cdn"] == "dropcdn"
        assert not any(
            "secret" in str(v) or "seg-1" in str(v) for v in errors[0].values()
        )

    def test_a_redirect_logs_no_video_url(self) -> None:
        link = CachedStreamLink(
            stream_id="abc123",
            hoster_url="https://voe.sx/e/abc123",
            hoster="voe",
            video_url="https://delivery.voe.sx/engine/secret-token/video.mp4?i=1.2.3.4",
            resolved_at=time.time(),
        )
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)
        client = TestClient(_make_app(stream_link_repo=repo), follow_redirects=False)

        with structlog.testing.capture_logs() as logs:
            client.get(f"{_PREFIX}/stremio/play/abc123")

        played = [e for e in logs if e["event"] == "stremio_play_resolved"]
        assert played and played[0]["cdn"] == "voe"
        assert not any("secret-token" in str(v) for v in played[0].values())

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_query_string_fallback_to_video_url(self, mock_fetch: AsyncMock) -> None:
        """When request has no query params, falls back to cached video_url's query."""
        link = _make_hls_link(
            video_url="https://cdn.example.com/hls/master.m3u8?token=secret123"
        )
        mock_fetch.return_value = (
            b"#EXTM3U\n#EXT-X-ENDLIST\n",
            "application/vnd.apple.mpegurl",
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8")

        assert resp.status_code == 200
        # Verify the CDN URL was built with the fallback query string
        call_args = mock_fetch.call_args
        target_url = call_args[0][1]
        assert "token=secret123" in target_url

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_request_query_takes_priority(self, mock_fetch: AsyncMock) -> None:
        """Request query params override video_url's query params."""
        link = _make_hls_link(
            video_url="https://cdn.example.com/hls/master.m3u8?token=old"
        )
        mock_fetch.return_value = (
            b"#EXTM3U\n#EXT-X-ENDLIST\n",
            "application/vnd.apple.mpegurl",
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?token=new")

        assert resp.status_code == 200
        call_args = mock_fetch.call_args
        target_url = call_args[0][1]
        assert "token=new" in target_url
        assert "token=old" not in target_url

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_cors_headers_on_all_responses(self, mock_fetch: AsyncMock) -> None:
        link = _make_hls_link()
        mock_fetch.return_value = (b"#EXTM3U\n", "application/vnd.apple.mpegurl")

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?t=abc")

        assert resp.headers.get("access-control-allow-origin") == "*"

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_headers_forwarded_to_cdn(self, mock_fetch: AsyncMock) -> None:
        """Stored video_headers are forwarded to CDN fetch."""
        link = _make_hls_link(
            video_headers={
                "Referer": "https://mysite.io/",
                "Origin": "https://mysite.io",
            }
        )
        mock_fetch.return_value = (b"#EXTM3U\n", "application/vnd.apple.mpegurl")

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo)
        client = TestClient(app)

        client.get(f"{_PREFIX}/stremio/proxy/hls-abc/master.m3u8?t=abc")

        call_args = mock_fetch.call_args
        forwarded_headers = call_args[0][2]
        assert forwarded_headers["Referer"] == "https://mysite.io/"
        assert forwarded_headers["Origin"] == "https://mysite.io"


class TestPinnedPlaylists:
    """All answers and devices share one stored link per hoster URL: a later
    resolution under its id moved a running playback's segments to another
    CDN node with the old token (403, then 502; code review, 2026-10-06). A
    served playlist points at a copy of the link it came from."""

    _PLAYLIST = b"#EXTM3U\n#EXTINF:10.0,\nseg-1.ts\n#EXT-X-ENDLIST\n"

    @staticmethod
    def _store() -> tuple[dict[str, CachedStreamLink], AsyncMock]:
        store: dict[str, CachedStreamLink] = {}
        repo = AsyncMock()
        repo.get = AsyncMock(side_effect=store.get)
        repo.save = AsyncMock(
            side_effect=lambda link: store.__setitem__(link.stream_id, link)
        )
        return store, repo

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_later_resolution_leaves_a_running_playback_alone(
        self, mock_fetch: AsyncMock, mock_segment: AsyncMock
    ) -> None:
        store, repo = self._store()
        store["hls-abc"] = replace(_make_hls_link(), resolved_at=time.time())
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")

        async def _segment() -> Any:
            yield b"ts"

        mock_segment.return_value = (_segment(), "video/mp2t")
        client = TestClient(_make_app(stream_link_repo=repo))

        playlist = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}").text
        segment = next(line for line in playlist.splitlines() if "seg-1.ts" in line)
        # Another device resolves the hoster URL to another CDN node
        store["hls-abc"] = replace(
            store["hls-abc"],
            video_url="https://node-b.dropcdn.io/hls2/09/video/master.m3u8?t=new",
        )

        resp = client.get(segment)

        assert resp.status_code == 200
        target = mock_segment.await_args.args[1]
        assert target.startswith("https://cdn.dropcdn.io/hls2/01/video/seg-1.ts")

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_one_resolution_makes_one_copy(self, mock_fetch: AsyncMock) -> None:
        store, repo = self._store()
        store["hls-abc"] = replace(_make_hls_link(), resolved_at=time.time())
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        client = TestClient(_make_app(stream_link_repo=repo))

        first = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}").text
        again = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}").text

        assert first == again
        assert sorted(store) == sorted({"hls-abc", *_pinned_ids(first)})
        assert len(_pinned_ids(first)) == 1


def _pinned_ids(playlist: str) -> set[str]:
    """The link ids a rewritten playlist points at."""
    return {
        line.split("/stremio/proxy/", 1)[1].split("/", 1)[0]
        for line in playlist.splitlines()
        if "/stremio/proxy/" in line
    }


def _refused(url: str, status: int = 403) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )


class TestProxyResolvesAgain:
    """A stream object Stremio kept (autoplay an hour later, Continue
    Watching days later) still plays: the playlist under its fixed name is
    the current one."""

    _MASTER = f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}"
    _PLAYLIST = b"#EXTM3U\n#EXTINF:10.0,\nseg-1.ts\n#EXT-X-ENDLIST\n"

    def _app(self, link: CachedStreamLink, resolved: ResolvedStream | None):
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)
        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=resolved)
        return _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_the_fixed_name_serves_the_stored_playlist(
        self, mock_fetch: AsyncMock
    ) -> None:
        link = replace(_make_hls_link(), resolved_at=time.time())
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        app = self._app(link, None)

        resp = TestClient(app).get(self._MASTER)

        assert resp.status_code == 200
        assert mock_fetch.call_args[0][1] == link.video_url
        assert "seg-1.ts" in resp.text
        app.state.hoster_resolver_registry.resolve.assert_not_awaited()

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_stale_playlist_resolves_again(self, mock_fetch: AsyncMock) -> None:
        link = replace(_make_hls_link(), resolved_at=time.time() - 2 * 3600)
        new = ResolvedStream(
            video_url="https://cdn.dropcdn.io/hls2/02/video/master.m3u8?t=new",
            is_hls=True,
            headers={"Referer": "https://dropload.io/"},
        )
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        app = self._app(link, new)

        resp = TestClient(app).get(self._MASTER)

        assert resp.status_code == 200
        assert mock_fetch.call_args[0][1] == new.video_url
        saved = app.state.stream_link_repo.save.await_args.args[0]
        assert saved.video_url == new.video_url

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_refused_playlist_resolves_again_once(
        self, mock_fetch: AsyncMock
    ) -> None:
        """An expired token: 403 from the CDN, then a new resolution past the
        resolver's cache."""
        link = replace(_make_hls_link(), resolved_at=time.time())
        new = ResolvedStream(
            video_url="https://cdn.dropcdn.io/hls2/02/video/master.m3u8?t=new",
            is_hls=True,
        )
        mock_fetch.side_effect = [
            _refused(link.video_url),
            (self._PLAYLIST, "application/vnd.apple.mpegurl"),
        ]
        app = self._app(link, new)

        resp = TestClient(app).get(self._MASTER)

        assert resp.status_code == 200
        assert mock_fetch.call_args[0][1] == new.video_url
        app.state.hoster_resolver_registry.resolve.assert_awaited_once_with(
            link.hoster_url, link.hoster, refresh=True
        )

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_refused_variant_is_not_resolved_again(
        self, mock_fetch: AsyncMock
    ) -> None:
        """Variants follow a playlist fetched moments before."""
        link = replace(_make_hls_link(), resolved_at=time.time())
        mock_fetch.side_effect = _refused(link.video_url)
        app = self._app(link, None)

        resp = TestClient(app).get(f"{_PREFIX}/stremio/proxy/hls-abc/index-v1.m3u8")

        assert resp.status_code == 502
        app.state.hoster_resolver_registry.resolve.assert_not_awaited()

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_stale_stream_the_hoster_no_longer_gives_plays_its_stored_url(
        self, mock_fetch: AsyncMock
    ) -> None:
        """3 FireStream links of the dev-server end-to-end run resolved no
        more after an hour; their stored playlists still played."""
        link = replace(_make_hls_link(), resolved_at=time.time() - 2 * 3600)
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        app = self._app(link, None)

        resp = TestClient(app).get(self._MASTER)

        assert resp.status_code == 200
        assert mock_fetch.call_args[0][1] == link.video_url

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_stream_the_hoster_and_the_cdn_no_longer_have_is_502(
        self, mock_fetch: AsyncMock
    ) -> None:
        link = replace(_make_hls_link(), resolved_at=time.time() - 2 * 3600)
        mock_fetch.side_effect = _refused(link.video_url)
        app = self._app(link, None)

        resp = TestClient(app).get(self._MASTER)

        assert resp.status_code == 502
        mock_fetch.assert_awaited_once()


def _make_file_link(
    *,
    stream_id: str = "file-abc",
    video_url: str = "https://s-delivery.mxdcontent.example/v/abc.mp4?s=tok&e=1",
    address_bound: bool = True,
    resolved_at: float | None = None,
) -> CachedStreamLink:
    """A stored MixDrop file, fresh unless *resolved_at* says otherwise."""
    return CachedStreamLink(
        stream_id=stream_id,
        hoster_url="https://mixdrop.ag/e/xyz",
        title="Test File",
        hoster="mixdrop",
        video_url=video_url,
        video_headers=json.dumps({"Referer": "https://mixdrop.ag/"}),
        resolved_at=time.time() if resolved_at is None else resolved_at,
        address_bound=address_bound,
    )


def _file_answer(
    status: int = 200,
    headers: dict[str, str] | None = None,
    chunks: tuple[bytes, ...] = (b"\x00" * 1000, b"\x01" * 500),
) -> FileAnswer:
    """What ``stream_file`` returns: the CDN's answer with the body in pieces."""

    async def _iter() -> Any:
        for chunk in chunks:
            yield chunk

    passed = headers or {
        "content-type": "video/mp4",
        "content-length": str(sum(len(chunk) for chunk in chunks)),
        "accept-ranges": "bytes",
    }
    return FileAnswer(status, passed, _iter())


class TestProxyFileEndpoint:
    """GET and HEAD /api/v1/stremio/proxy/{stream_id}/file: an address-bound
    direct file streamed with the player's byte range."""

    _FILE = f"{_PREFIX}/stremio/proxy/file-abc/{FILE_NAME}"

    def _app(
        self, link: CachedStreamLink | None, resolved: ResolvedStream | None = None
    ) -> FastAPI:
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)
        registry = AsyncMock()
        registry.resolve = AsyncMock(return_value=resolved)
        return _make_app(stream_link_repo=repo, hoster_resolver_registry=registry)

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_whole_file(self, mock_stream: AsyncMock) -> None:
        mock_stream.return_value = _file_answer(
            headers={
                "content-type": "video/mp4",
                "content-length": "1500000000",
                "accept-ranges": "bytes",
            }
        )
        link = _make_file_link()

        resp = TestClient(self._app(link)).get(self._FILE)

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.headers["content-length"] == "1500000000"
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.headers.get("access-control-allow-origin") == "*"
        assert resp.content == b"\x00" * 1000 + b"\x01" * 500
        args, kwargs = mock_stream.await_args.args, mock_stream.await_args.kwargs
        assert args[1] == link.video_url
        assert args[2] == {"Referer": "https://mixdrop.ag/"}
        assert kwargs["player"].get("range") is None
        assert kwargs["head"] is False

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_range(self, mock_stream: AsyncMock) -> None:
        mock_stream.return_value = _file_answer(
            206,
            headers={
                "content-type": "video/mp4",
                "content-range": "bytes 1000000-1499999999/1500000000",
                "content-length": "1499000000",
            },
        )

        resp = TestClient(self._app(_make_file_link())).get(
            self._FILE, headers={"Range": "bytes=1000000-"}
        )

        assert resp.status_code == 206
        assert resp.headers["content-range"] == "bytes 1000000-1499999999/1500000000"
        assert resp.headers["content-length"] == "1499000000"
        assert mock_stream.await_args.kwargs["player"]["range"] == "bytes=1000000-"

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_head(self, mock_stream: AsyncMock) -> None:
        mock_stream.return_value = _file_answer(chunks=())

        resp = TestClient(self._app(_make_file_link())).head(self._FILE)

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == b""
        assert mock_stream.await_args.kwargs["head"] is True

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_stale_link_resolves_again_first(self, mock_stream: AsyncMock) -> None:
        mock_stream.return_value = _file_answer()
        link = _make_file_link(resolved_at=time.time() - 2 * 3600)
        new = ResolvedStream(
            video_url="https://s-delivery.mxdcontent.example/v/abc.mp4?s=new",
            address_bound=True,
        )
        app = self._app(link, new)

        resp = TestClient(app).get(self._FILE)

        assert resp.status_code == 200
        assert mock_stream.await_args.args[1] == new.video_url
        saved = app.state.stream_link_repo.save.await_args.args[0]
        assert saved.video_url == new.video_url

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_an_expired_url_is_resolved_again_once(
        self, mock_stream: AsyncMock
    ) -> None:
        """FSST answers 410 for an expired URL: one resolution past the
        resolver's cache, then the file from the new URL."""
        link = _make_file_link()
        new = ResolvedStream(
            video_url="https://s-delivery.mxdcontent.example/v/abc.mp4?s=new",
            address_bound=True,
        )
        mock_stream.side_effect = [
            _refused(link.video_url, 410),
            _file_answer(
                206,
                headers={
                    "content-type": "video/mp4",
                    "content-range": "bytes 0-1499/1500",
                    "content-length": "1500",
                },
            ),
        ]
        app = self._app(link, new)

        resp = TestClient(app).get(self._FILE, headers={"Range": "bytes=0-"})

        assert resp.status_code == 206
        assert mock_stream.await_args.args[1] == new.video_url
        app.state.hoster_resolver_registry.resolve.assert_awaited_once_with(
            link.hoster_url, link.hoster, refresh=True
        )

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_refusal_that_persists_is_502(self, mock_stream: AsyncMock) -> None:
        link = _make_file_link()
        new = ResolvedStream(video_url=link.video_url, address_bound=True)
        mock_stream.side_effect = [_refused(link.video_url), _refused(link.video_url)]

        resp = TestClient(self._app(link, new)).get(self._FILE)

        assert resp.status_code == 502
        assert mock_stream.await_count == 2

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_refusal_without_a_new_resolution_is_502(
        self, mock_stream: AsyncMock
    ) -> None:
        link = _make_file_link()
        mock_stream.side_effect = _refused(link.video_url, 404)

        resp = TestClient(self._app(link, None)).get(self._FILE)

        assert resp.status_code == 502
        mock_stream.assert_awaited_once()

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_cdn_that_cannot_be_reached_is_502(self, mock_stream: AsyncMock) -> None:
        mock_stream.side_effect = httpx.ConnectError("down")

        resp = TestClient(self._app(_make_file_link())).get(self._FILE)

        assert resp.status_code == 502

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_an_hls_link_is_refused(self, mock_stream: AsyncMock) -> None:
        resp = TestClient(self._app(_make_hls_link(stream_id="file-abc"))).get(
            self._FILE
        )

        assert resp.status_code == 400
        mock_stream.assert_not_awaited()

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_record_from_before_the_flag_is_refused(
        self, mock_stream: AsyncMock
    ) -> None:
        """Such a link plays through /play, as before."""
        resp = TestClient(self._app(_make_file_link(address_bound=False))).get(
            self._FILE
        )

        assert resp.status_code == 400
        mock_stream.assert_not_awaited()

    def test_not_found(self) -> None:
        resp = TestClient(self._app(None)).get(self._FILE)

        assert resp.status_code == 404


class TestProxyLeavesHlsToThePlayer:
    """Stremio Web lets its streaming server probe every stream. An HLS
    source then goes through the server's converter, which re-encodes the
    video (it repackages MP4 and Matroska only): on a Raspberry Pi 4 too
    slow for 1080p. The proxy refuses the converter's ffmpeg the playlist,
    and Stremio Web plays it itself."""

    _MASTER = f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}"
    _FFMPEG = {"User-Agent": "Lavf/60.16.100"}
    _BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0"}
    _PLAYLIST = b"#EXTM3U\n#EXTINF:10.0,\nseg-1.ts\n#EXT-X-ENDLIST\n"

    def _app(self, config: StremioConfig) -> FastAPI:
        repo = AsyncMock()
        repo.get = AsyncMock(
            return_value=replace(_make_hls_link(), resolved_at=time.time())
        )
        app = _make_app(stream_link_repo=repo)
        app.state.config.stremio = config
        return app

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_the_streaming_servers_ffmpeg_gets_no_playlist(
        self, mock_fetch: AsyncMock
    ) -> None:
        app = self._app(StremioConfig())

        resp = TestClient(app).get(self._MASTER, headers=self._FFMPEG)

        assert resp.status_code == 403
        mock_fetch.assert_not_awaited()
        app.state.stream_link_repo.get.assert_not_awaited()

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_the_player_gets_the_playlist(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        client = TestClient(self._app(StremioConfig()))

        head = client.head(self._MASTER, headers=self._BROWSER)
        resp = client.get(self._MASTER, headers=self._BROWSER)

        assert head.status_code == 200
        assert head.headers["content-type"] == "application/vnd.apple.mpegurl"
        assert resp.status_code == 200
        assert "seg-1.ts" in resp.text

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_variants_are_not_refused(self, mock_fetch: AsyncMock) -> None:
        """Only the stream's playlist decides who plays it."""
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        client = TestClient(self._app(StremioConfig()))

        resp = client.get(
            f"{_PREFIX}/stremio/proxy/hls-abc/index-v1.m3u8", headers=self._FFMPEG
        )

        assert resp.status_code == 200

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_a_server_may_transcode_when_allowed(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        app = self._app(StremioConfig(allow_hls_transcoding=True))

        resp = TestClient(app).get(self._MASTER, headers=self._FFMPEG)

        assert resp.status_code == 200


class TestProxyTelemetry:
    """Each proxy request is recorded by kind and answer status; segments
    with the bytes sent."""

    _PLAYLIST = b"#EXTM3U\n#EXTINF:10.0,\nseg-1.ts\n#EXT-X-ENDLIST\n"

    def _client(self, link: Any) -> tuple[TestClient, Telemetry]:
        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)
        app = _make_app(stream_link_repo=repo)
        app.state.config.stremio = StremioConfig()
        app.state.telemetry = Telemetry()
        return TestClient(app), app.state.telemetry

    @staticmethod
    def _total(t: Telemetry, kind: str, outcome: str) -> float | None:
        return t.registry.get_sample_value(
            "scavengarr_hls_proxy_total", {"kind": kind, "outcome": outcome}
        )

    @staticmethod
    def _bytes(t: Telemetry, kind: str) -> float | None:
        return t.registry.get_sample_value(
            "scavengarr_hls_proxy_bytes_total", {"kind": kind}
        )

    @patch(f"{_PROXY_MODULE}.fetch_hls_resource", new_callable=AsyncMock)
    def test_playlists(self, mock_fetch: AsyncMock) -> None:
        mock_fetch.return_value = (self._PLAYLIST, "application/vnd.apple.mpegurl")
        client, t = self._client(replace(_make_hls_link(), resolved_at=time.time()))

        master = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}")
        client.get(f"{_PREFIX}/stremio/proxy/hls-abc/index-v1.m3u8")

        assert self._total(t, "master", "200") == 1
        assert self._total(t, "playlist", "200") == 1
        assert self._bytes(t, "master") == len(master.content)
        seconds = t.registry.get_sample_value(
            "scavengarr_hls_proxy_seconds_count", {"kind": "master"}
        )
        assert seconds == 1

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    def test_segment_bytes(self, mock_stream: AsyncMock) -> None:
        async def _chunks() -> Any:
            yield b"\x00" * 1000
            yield b"\x01" * 500

        mock_stream.return_value = (_chunks(), "video/mp2t")
        client, t = self._client(_make_hls_link())

        resp = client.get(f"{_PREFIX}/stremio/proxy/hls-abc/seg-1.ts?t=abc")

        assert len(resp.content) == 1500
        assert self._total(t, "segment", "200") == 1
        assert self._bytes(t, "segment") == 1500

    @patch(f"{_PROXY_MODULE}.stream_hls_segment", new_callable=AsyncMock)
    def test_a_head_request_counts_no_segment_bytes(
        self, mock_stream: AsyncMock
    ) -> None:
        async def _chunks() -> Any:
            yield b"\x00" * 1000

        mock_stream.return_value = (_chunks(), "video/mp2t")
        client, t = self._client(_make_hls_link())

        resp = client.head(f"{_PREFIX}/stremio/proxy/hls-abc/seg-1.ts?t=abc")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp2t"
        assert mock_stream.await_args.kwargs["head"] is True
        assert self._bytes(t, "segment") is None

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_file_bytes(self, mock_stream: AsyncMock) -> None:
        """A proxied file is recorded on the HLS proxy's metrics as ``file``."""
        mock_stream.return_value = _file_answer(
            206,
            headers={
                "content-type": "video/mp4",
                "content-range": "bytes 0-3145727/3145728",
                "content-length": "3145728",
            },
            chunks=(b"\x00" * 1048576,) * 3,
        )
        client, t = self._client(_make_file_link())

        resp = client.get(
            f"{_PREFIX}/stremio/proxy/file-abc/{FILE_NAME}",
            headers={"Range": "bytes=0-"},
        )

        assert resp.status_code == 206
        assert len(resp.content) == 3 * 1048576
        assert self._total(t, "file", "206") == 1
        assert self._bytes(t, "file") == 3 * 1048576

    @patch(f"{_PROXY_MODULE}.stream_file", new_callable=AsyncMock)
    def test_a_head_request_counts_no_file_bytes(self, mock_stream: AsyncMock) -> None:
        mock_stream.return_value = _file_answer(chunks=())
        client, t = self._client(_make_file_link())

        resp = client.head(f"{_PREFIX}/stremio/proxy/file-abc/{FILE_NAME}")

        assert resp.status_code == 200
        assert self._total(t, "file", "200") == 1
        assert self._bytes(t, "file") is None

    def test_converter_refused(self) -> None:
        client, t = self._client(_make_hls_link())

        client.get(
            f"{_PREFIX}/stremio/proxy/hls-abc/{HLS_MASTER}",
            headers={"User-Agent": "Lavf/60.16.100"},
        )

        assert self._total(t, "master", "403") == 1

    def test_unknown_stream(self) -> None:
        client, t = self._client(None)

        client.get(f"{_PREFIX}/stremio/proxy/hls-abc/seg-1.ts")

        assert self._total(t, "segment", "404") == 1
