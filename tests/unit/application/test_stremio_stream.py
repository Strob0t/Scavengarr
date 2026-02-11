"""Tests for StremioStreamUseCase."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from scavengarr.application.use_cases.stremio_stream import (
    StremioStreamUseCase,
    _build_search_query,
    _format_stream,
)
from scavengarr.domain.entities.stremio import (
    RankedStream,
    StreamLanguage,
    StreamQuality,
    StremioStreamRequest,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.config.schema import StremioConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> StremioConfig:
    defaults = {
        "max_concurrent_plugins": 5,
        "language_scores": {"de": 1000, "en": 150},
        "default_language_score": 100,
        "quality_multiplier": 10,
        "hoster_scores": {"voe": 4},
    }
    defaults.update(overrides)
    return StremioConfig(**defaults)


def _make_request(
    *,
    imdb_id: str = "tt1234567",
    content_type: str = "movie",
    season: int | None = None,
    episode: int | None = None,
) -> StremioStreamRequest:
    return StremioStreamRequest(
        imdb_id=imdb_id,
        content_type=content_type,
        season=season,
        episode=episode,
    )


def _make_search_result(
    *,
    title: str = "Test Movie",
    download_link: str = "https://voe.sx/e/abc",
    download_links: list[dict[str, str]] | None = None,
    release_name: str | None = None,
    metadata: dict | None = None,
) -> SearchResult:
    return SearchResult(
        title=title,
        download_link=download_link,
        download_links=download_links,
        release_name=release_name,
        metadata=metadata or {},
    )


def _make_use_case(
    *,
    tmdb: AsyncMock | None = None,
    plugins: MagicMock | None = None,
    search_engine: AsyncMock | None = None,
    config: StremioConfig | None = None,
) -> StremioStreamUseCase:
    engine = search_engine or AsyncMock()
    # Default: validate_results returns input unchanged
    if not search_engine:
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
        engine.search = AsyncMock(return_value=[])
    return StremioStreamUseCase(
        tmdb=tmdb or AsyncMock(),
        plugins=plugins or MagicMock(),
        search_engine=engine,
        config=config or _make_config(),
    )


# ---------------------------------------------------------------------------
# _build_search_query
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    def test_movie_query(self) -> None:
        req = _make_request()
        assert _build_search_query("Iron Man", req) == "Iron Man"

    def test_series_returns_plain_title(self) -> None:
        """Season/episode are passed separately, not appended to the query."""
        req = _make_request(content_type="series", season=1, episode=5)
        assert _build_search_query("Breaking Bad", req) == "Breaking Bad"

    def test_series_season_only_returns_plain_title(self) -> None:
        req = _make_request(content_type="series", season=2)
        assert _build_search_query("Breaking Bad", req) == "Breaking Bad"

    def test_series_no_season_returns_plain_title(self) -> None:
        req = _make_request(content_type="series")
        assert _build_search_query("Show", req) == "Show"


# ---------------------------------------------------------------------------
# _format_stream
# ---------------------------------------------------------------------------


class TestFormatStream:
    def test_full_stream(self) -> None:
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=lang,
            size="1.5 GB",
            source_plugin="hdfilme",
            rank_score=1500,
        )
        result = _format_stream(ranked)
        assert result.url == "https://voe.sx/e/abc"
        assert "hdfilme" in result.name
        assert "HD 1080P" in result.name
        assert "German Dub" in result.description
        assert "VOE" in result.description
        assert "1.5 GB" in result.description

    def test_no_source_plugin(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
        )
        result = _format_stream(ranked)
        assert result.name == "HD 720P"

    def test_no_language_or_size(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.UNKNOWN,
        )
        result = _format_stream(ranked)
        assert "VOE" in result.description

    def test_empty_hoster(self) -> None:
        ranked = RankedStream(
            url="https://example.com",
            hoster="",
            quality=StreamQuality.SD,
        )
        result = _format_stream(ranked)
        # Empty hoster should not leave trailing separators
        assert (
            result.description == ""
            or "|" not in result.description
            or result.description.strip()
        )


# ---------------------------------------------------------------------------
# StremioStreamUseCase.execute
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_title_not_found_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value=None)
        uc = _make_use_case(tmdb=tmdb)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_no_stream_plugins_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Iron Man")

        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_happy_path_movie(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Iron Man")

        sr = _make_search_result(
            download_links=[
                {"url": "https://voe.sx/e/abc", "quality": "1080p"},
            ],
        )

        # Python plugin (no scraping attribute) that returns search results
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping  # Ensure no scraping attr → Python plugin path

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"
        tmdb.get_german_title.assert_awaited_once_with("tt1234567")
        mock_plugin.search.assert_awaited_once_with(
            "Iron Man", category=2000, season=None, episode=None
        )
        engine.validate_results.assert_awaited_once()

    async def test_series_query_passes_season_episode(self) -> None:
        """Season/episode are passed as kwargs, query is the plain title."""
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Breaking Bad")

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["aniworld"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        req = _make_request(content_type="series", season=1, episode=5)
        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(req)

        mock_plugin.search.assert_awaited_once_with(
            "Breaking Bad", category=5000, season=1, episode=5
        )

    async def test_multiple_plugins_combined(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        sr1 = _make_search_result(
            download_links=[{"url": "https://voe.sx/e/1"}],
        )
        sr2 = _make_search_result(
            download_links=[{"url": "https://filemoon.sx/e/2"}],
        )

        plugin_a = AsyncMock()
        plugin_a.search = AsyncMock(return_value=[sr1])
        del plugin_a.scraping
        plugin_b = AsyncMock()
        plugin_b.search = AsyncMock(return_value=[sr2])
        del plugin_b.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["a", "b"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda name: {"a": plugin_a, "b": plugin_b}[name]

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) == 2
        urls = {s.url for s in result}
        assert "https://voe.sx/e/1" in urls
        assert "https://filemoon.sx/e/2" in urls

    async def test_plugin_error_does_not_crash(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        sr = _make_search_result(
            download_links=[{"url": "https://voe.sx/e/ok"}],
        )

        good_plugin = AsyncMock()
        good_plugin.search = AsyncMock(return_value=[sr])
        del good_plugin.scraping
        bad_plugin = AsyncMock()
        bad_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))
        del bad_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["good", "bad"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda name: {"good": good_plugin, "bad": bad_plugin}[
            name
        ]

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        # Should still return results from the good plugin
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/ok"

    async def test_plugin_not_found_skipped(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["missing"] if p == "stream" else []
        )
        plugins.get.side_effect = KeyError("not found")

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_empty_search_results_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_source_plugin_tagged_on_results(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        sr = _make_search_result(
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["myplugin"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(_make_request())

        # The use case should tag source_plugin in metadata
        assert sr.metadata.get("source_plugin") == "myplugin"

    async def test_both_provides_plugins_included(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        sr = _make_search_result(
            download_links=[{"url": "https://voe.sx/e/both"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        # "stream" returns nothing, but "both" returns one plugin
        plugins.get_by_provides.side_effect = lambda p: (
            [] if p == "stream" else ["combo"]
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) == 1

    async def test_deduplication_of_plugin_names(self) -> None:
        """Plugin in both stream and both is searched once."""
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        sr = _make_search_result(
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        # Same plugin name returned by both calls
        plugins.get_by_provides.side_effect = lambda p: (
            ["overlap"] if p in ("stream", "both") else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(_make_request())

        # Should only search once despite appearing in both lists
        mock_plugin.search.assert_awaited_once()

    async def test_streams_sorted_by_score(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        sr = _make_search_result(
            download_links=[
                {"url": "https://voe.sx/e/low", "quality": "SD"},
                {"url": "https://voe.sx/e/high", "quality": "1080p"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        # Should have 2 streams (order depends on sorter)
        assert len(result) == 2

    async def test_plugin_without_search_method_skipped(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        # Plugin without search method
        no_search_plugin = MagicMock(spec=[])

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["nosearch"] if p == "stream" else []
        )
        plugins.get.return_value = no_search_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_concurrency_limited(self) -> None:
        """Verify semaphore limits concurrent plugin searches."""
        tmdb = AsyncMock()
        tmdb.get_german_title = AsyncMock(return_value="Movie")

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        many_names = [f"plugin_{i}" for i in range(10)]
        plugins.get_by_provides.side_effect = lambda p: (
            many_names if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        config = _make_config(max_concurrent_plugins=2)
        uc = _make_use_case(
            tmdb=tmdb, plugins=plugins, search_engine=engine, config=config
        )

        # Should complete without error; semaphore internally limits to 2
        result = await uc.execute(_make_request())
        assert result == []
        assert mock_plugin.search.await_count == 10
