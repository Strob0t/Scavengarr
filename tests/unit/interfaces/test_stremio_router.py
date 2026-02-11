"""Tests for Stremio addon router endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.domain.entities.stremio import StremioMetaPreview, StremioStream
from scavengarr.interfaces.api.stremio.router import (
    _parse_stream_id,
    router,
)


def _make_app(
    *,
    plugin_names: list[str] | None = None,
    stremio_stream_uc: AsyncMock | None = None,
    stremio_catalog_uc: AsyncMock | None = None,
) -> FastAPI:
    """Create a minimal FastAPI app with the stremio router."""
    app = FastAPI()
    app.include_router(router)

    plugins = MagicMock()
    names = plugin_names or []
    plugins.get_by_provides.return_value = names
    app.state.plugins = plugins

    app.state.stremio_stream_uc = stremio_stream_uc
    app.state.stremio_catalog_uc = stremio_catalog_uc

    return app


class TestParseStreamId:
    def test_movie_id(self) -> None:
        result = _parse_stream_id("movie", "tt1234567")
        assert result is not None
        assert result.imdb_id == "tt1234567"
        assert result.content_type == "movie"
        assert result.season is None
        assert result.episode is None

    def test_series_id_with_season_episode(self) -> None:
        result = _parse_stream_id("series", "tt1234567:1:5")
        assert result is not None
        assert result.imdb_id == "tt1234567"
        assert result.content_type == "series"
        assert result.season == 1
        assert result.episode == 5

    def test_invalid_prefix(self) -> None:
        result = _parse_stream_id("movie", "nm1234567")
        assert result is None

    def test_invalid_content_type(self) -> None:
        result = _parse_stream_id("channel", "tt1234567")
        assert result is None

    def test_series_without_season_episode(self) -> None:
        result = _parse_stream_id("series", "tt1234567")
        assert result is not None
        assert result.season is None
        assert result.episode is None

    def test_series_non_numeric_season(self) -> None:
        result = _parse_stream_id("series", "tt1234567:abc:5")
        assert result is None

    def test_tmdb_movie_id(self) -> None:
        result = _parse_stream_id("movie", "tmdb:12345")
        assert result is not None
        assert result.imdb_id == "tmdb:12345"
        assert result.content_type == "movie"
        assert result.season is None
        assert result.episode is None

    def test_tmdb_series_id_with_season_episode(self) -> None:
        result = _parse_stream_id("series", "tmdb:67890:2:10")
        assert result is not None
        assert result.imdb_id == "tmdb:67890"
        assert result.content_type == "series"
        assert result.season == 2
        assert result.episode == 10

    def test_tmdb_series_without_season_episode(self) -> None:
        result = _parse_stream_id("series", "tmdb:67890")
        assert result is not None
        assert result.imdb_id == "tmdb:67890"
        assert result.season is None
        assert result.episode is None

    def test_tmdb_invalid_content_type(self) -> None:
        result = _parse_stream_id("channel", "tmdb:12345")
        assert result is None

    def test_tmdb_series_non_numeric_season(self) -> None:
        result = _parse_stream_id("series", "tmdb:12345:abc:5")
        assert result is None


class TestManifestEndpoint:
    def test_returns_valid_manifest(self) -> None:
        app = _make_app(plugin_names=["hdfilme", "kinox"])
        client = TestClient(app)

        resp = client.get("/stremio/manifest.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "community.scavengarr"
        assert "movie" in data["types"]
        assert "series" in data["types"]
        assert "stream" in data["resources"]
        assert "catalog" in data["resources"]
        assert "tt" in data["idPrefixes"]
        assert "tmdb:" in data["idPrefixes"]

    def test_cors_headers(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/stremio/manifest.json")

        assert resp.headers["access-control-allow-origin"] == "*"


class TestCatalogEndpoint:
    def test_returns_empty_when_no_catalog_uc(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/stremio/catalog/movie/scavengarr-trending-movies.json")

        assert resp.status_code == 200
        assert resp.json() == {"metas": []}

    def test_returns_trending_movies(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(
            return_value=[
                StremioMetaPreview(
                    id="tt1234567",
                    type="movie",
                    name="Test Movie",
                    poster="https://image.tmdb.org/t/p/w500/abc.jpg",
                    description="A test movie",
                    release_info="2024",
                    imdb_rating="7.5",
                ),
            ]
        )
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get("/stremio/catalog/movie/scavengarr-trending-movies.json")

        assert resp.status_code == 200
        metas = resp.json()["metas"]
        assert len(metas) == 1
        assert metas[0]["id"] == "tt1234567"
        assert metas[0]["name"] == "Test Movie"

    def test_returns_trending_series(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.trending = AsyncMock(
            return_value=[
                StremioMetaPreview(
                    id="tt7654321",
                    type="series",
                    name="Test Show",
                ),
            ]
        )
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get("/stremio/catalog/series/scavengarr-trending-series.json")

        assert resp.status_code == 200
        metas = resp.json()["metas"]
        assert len(metas) == 1
        assert metas[0]["name"] == "Test Show"

    def test_invalid_content_type_returns_empty(self) -> None:
        catalog_uc = AsyncMock()
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get("/stremio/catalog/channel/foo.json")

        assert resp.status_code == 200
        assert resp.json() == {"metas": []}


class TestCatalogSearchEndpoint:
    def test_search_movies(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(
            return_value=[
                StremioMetaPreview(
                    id="tt0137523",
                    type="movie",
                    name="Fight Club",
                    poster="https://image.tmdb.org/t/p/w500/poster.jpg",
                ),
            ]
        )
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            "/stremio/catalog/movie/scavengarr-trending-movies/search=Fight Club.json"
        )

        assert resp.status_code == 200
        metas = resp.json()["metas"]
        assert len(metas) == 1
        assert metas[0]["name"] == "Fight Club"

    def test_search_series(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(
            return_value=[
                StremioMetaPreview(
                    id="tt0903747",
                    type="series",
                    name="Breaking Bad",
                ),
            ]
        )
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        url = (
            "/stremio/catalog/series"
            "/scavengarr-trending-series/search=Breaking Bad.json"
        )
        resp = client.get(url)

        assert resp.status_code == 200
        metas = resp.json()["metas"]
        assert len(metas) == 1
        assert metas[0]["name"] == "Breaking Bad"

    def test_empty_query_returns_empty(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(return_value=[])
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            "/stremio/catalog/movie/scavengarr-trending-movies/search= .json"
        )

        assert resp.status_code == 200
        assert resp.json() == {"metas": []}

    def test_no_catalog_uc_returns_empty(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.get(
            "/stremio/catalog/movie/scavengarr-trending-movies/search=Matrix.json"
        )

        assert resp.status_code == 200
        assert resp.json() == {"metas": []}

    def test_cors_headers(self) -> None:
        catalog_uc = AsyncMock()
        catalog_uc.search = AsyncMock(return_value=[])
        app = _make_app(stremio_catalog_uc=catalog_uc)
        client = TestClient(app)

        resp = client.get(
            "/stremio/catalog/movie/scavengarr-trending-movies/search=Test.json"
        )

        assert resp.headers["access-control-allow-origin"] == "*"


class TestStreamEndpoint:
    def test_returns_empty_for_invalid_id(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/stremio/stream/movie/nm1234567.json")

        assert resp.status_code == 200
        assert resp.json() == {"streams": []}

    def test_returns_empty_when_no_stream_uc(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json() == {"streams": []}

    def test_returns_streams_for_movie(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(
            return_value=[
                StremioStream(
                    name="hdfilme HD 1080P",
                    description="German Dub | VOE | 4.5 GB",
                    url="https://voe.sx/e/abc123",
                ),
            ]
        )

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get("/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) == 1
        assert streams[0]["url"] == "https://voe.sx/e/abc123"
        assert streams[0]["name"] == "hdfilme HD 1080P"
        assert streams[0]["description"] == "German Dub | VOE | 4.5 GB"

    def test_cors_headers_on_stream(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/stremio/stream/movie/tt1234567.json")

        assert resp.headers["access-control-allow-origin"] == "*"

    def test_returns_streams_for_tmdb_movie(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(
            return_value=[
                StremioStream(
                    name="hdfilme HD 1080P",
                    description="German Dub | VOE | 5.0 GB",
                    url="https://voe.sx/e/xyz789",
                ),
            ]
        )

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get("/stremio/stream/movie/tmdb:238.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) >= 1
        assert "url" in streams[0]
        # Verify the parsed request was passed to use case
        stream_uc.execute.assert_awaited_once()
        call_arg = stream_uc.execute.call_args[0][0]
        assert call_arg.imdb_id == "tmdb:238"
        assert call_arg.content_type == "movie"

    def test_tmdb_series_resolves_with_season_episode(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get("/stremio/stream/series/tmdb:1396:3:7.json")

        assert resp.status_code == 200
        stream_uc.execute.assert_awaited_once()
        call_arg = stream_uc.execute.call_args[0][0]
        assert call_arg.imdb_id == "tmdb:1396"
        assert call_arg.season == 3
        assert call_arg.episode == 7

    def test_series_search_includes_season_episode(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

        app = _make_app(stremio_stream_uc=stream_uc)
        client = TestClient(app)

        resp = client.get("/stremio/stream/series/tt0903747:1:1.json")

        assert resp.status_code == 200
        stream_uc.execute.assert_awaited_once()
        call_arg = stream_uc.execute.call_args[0][0]
        assert call_arg.imdb_id == "tt0903747"
        assert call_arg.season == 1
        assert call_arg.episode == 1

    def test_returns_empty_when_no_plugins(self) -> None:
        stream_uc = AsyncMock()
        stream_uc.execute = AsyncMock(return_value=[])

        app = _make_app(stremio_stream_uc=stream_uc, plugin_names=[])
        client = TestClient(app)

        resp = client.get("/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json() == {"streams": []}
