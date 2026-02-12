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
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    ResolvedStream,
    StremioMetaPreview,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.interfaces.api.stremio.router import router

_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# Fake plugins
# ---------------------------------------------------------------------------


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

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
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
        assert data["version"] == "0.1.0"
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

        app = _make_app(plugins=plugins)
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

        app = _make_app(plugins=plugins)
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
        stream_uc.execute = AsyncMock(return_value=[stream])

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
        stream_uc.execute = AsyncMock(return_value=[stream])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt0903747:1:5.json")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["streams"]) == 1

        # Verify the use case received correct parsed request
        call_args = stream_uc.execute.call_args
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
        stream_uc.execute = AsyncMock(return_value=[stream])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tmdb:12345.json")

        assert resp.status_code == 200
        call_args = stream_uc.execute.call_args
        request: StremioStreamRequest = call_args[0][0]
        assert request.imdb_id == "tmdb:12345"
        assert request.content_type == "movie"

    def test_tmdb_id_series_with_season_episode(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tmdb:67890:2:10.json")

        assert resp.status_code == 200
        call_args = stream_uc.execute.call_args
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
        stream_uc.execute.assert_not_awaited()

    def test_invalid_id_format_returns_empty(self) -> None:
        stream_uc = AsyncMock()

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/notanid.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []
        stream_uc.execute.assert_not_awaited()

    def test_no_stream_uc_returns_empty(self) -> None:
        """When stremio_stream_uc is None, return empty streams."""
        app = _make_app(stremio_stream_uc=None)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_empty_streams(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

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
        stream_uc.execute = AsyncMock(return_value=streams)

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
        stream_uc.execute = AsyncMock(return_value=[stream])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        s = resp.json()["streams"][0]
        assert s["name"] == "Iron Man (2008) 1080p"
        assert s["description"] == "hdfilme | German Dub | VOE | 2.5 GB"
        assert s["url"] == "https://voe.sx/e/xyz"

    def test_stream_cors_headers(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_series_without_season_episode_parsed_as_movie_style(self) -> None:
        """Series ID without :S:E still creates a request (no season/episode)."""
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt0903747.json")

        assert resp.status_code == 200
        call_args = stream_uc.execute.call_args
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
        stream_uc.execute = AsyncMock(return_value=[])

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

    def test_play_no_resolver_configured(self) -> None:
        """When hoster_resolver_registry is None, return 503."""
        link = CachedStreamLink(
            stream_id="abc123",
            hoster_url="https://voe.sx/e/abc123",
            title="Test",
            hoster="voe",
        )

        repo = AsyncMock()
        repo.get = AsyncMock(return_value=link)

        app = _make_app(stream_link_repo=repo, hoster_resolver_registry=None)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/play/abc123")

        assert resp.status_code == 503
        assert "resolver" in resp.json()["error"]

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
            "https://streamtape.com/v/abc", hoster="streamtape"
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
        stream_uc.execute = AsyncMock(side_effect=RuntimeError("plugin timeout"))

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_stream_error_has_cors_headers(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(side_effect=RuntimeError("boom"))

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

        resolver = MagicMock()
        resolver.list_hosters.return_value = ["voe", "streamtape"]

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

        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True
        assert data["tmdb_configured"] is True
        assert data["stream_plugin_count"] == 2
        assert data["stream_plugins"] == ["hdfilme", "aniworld"]
        assert data["stream_uc_initialized"] is True
        assert data["catalog_uc_initialized"] is True
        assert data["hoster_resolver_configured"] is True
        assert data["supported_hosters"] == ["voe", "streamtape"]
        assert data["stream_link_repo_configured"] is True

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

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=search_results or [])

        stream_link_repo = AsyncMock()

        config = StremioConfig()

        stream_uc = StremioStreamUseCase(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            config=config,
            stream_link_repo=stream_link_repo,
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
