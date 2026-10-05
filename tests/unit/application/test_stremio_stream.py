"""Tests for StremioStreamUseCase."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from scavengarr.application.stremio.search_cache import STALE_SECONDS, CachedSearch
from scavengarr.application.use_cases.stremio_stream import (
    StremioStreamUseCase,
)
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    ResolvedStream,
    StreamQuality,
    StremioStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.episode_filter import filter_by_episode
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match
from scavengarr.infrastructure.telemetry import Telemetry
from scavengarr.infrastructure.telemetry.tracing import Tracing

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
    stream_link_repo: AsyncMock | None = None,
    resolve_fn: Callable[..., Awaitable[ResolvedStream | None]] | None = None,
    cached_resolution_fn: Callable[[str], tuple[bool, ResolvedStream | None]]
    | None = None,
    cache: AsyncMock | None = None,
    search_ttl_seconds: int = 0,
    telemetry: TelemetryPort = NO_TELEMETRY,
) -> StremioStreamUseCase:
    engine = search_engine or AsyncMock()
    # Default: validate_results returns input unchanged
    if not search_engine:
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
        engine.search = AsyncMock(return_value=[])
    cfg = config or _make_config()
    if plugins is None:
        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
    return StremioStreamUseCase(
        tmdb=tmdb or AsyncMock(),
        plugins=plugins,
        search_engine=engine,
        config=cfg,
        sorter=StreamSorter(cfg),
        convert_fn=convert_search_results,
        filter_fn=filter_by_title_match,
        episode_filter_fn=filter_by_episode,
        user_agent=DEFAULT_USER_AGENT,
        max_results_var=search_max_results,
        stream_link_repo=stream_link_repo,
        resolve_fn=resolve_fn,
        cached_resolution_fn=cached_resolution_fn,
        pool=ConcurrencyPool(httpx_slots=100, pw_slots=100),
        cache=cache,
        search_ttl_seconds=search_ttl_seconds,
        telemetry=telemetry,
    )


# ---------------------------------------------------------------------------
# StremioStreamUseCase.execute
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_title_not_found_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=None)
        uc = _make_use_case(tmdb=tmdb)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_no_stream_plugins_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.return_value = []

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_happy_path_movie(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc", "quality": "1080p"},
            ],
        )

        # Python plugin (no scraping attribute) that returns search results
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping  # Ensure no scraping attr → Python plugin path
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"
        # Name should include reference title from TMDB
        assert result[0].description.startswith("Iron Man")
        assert result[0].name == "Scavengarr\n1080p"
        tmdb.get_title_and_year.assert_awaited_once_with("tt1234567", language="de")
        mock_plugin.isolated_search.assert_awaited_once_with(
            "Iron Man", 2000, season=None, episode=None
        )
        engine.validate_results.assert_awaited_once()

    async def test_series_query_passes_season_episode(self) -> None:
        """Season/episode are passed as kwargs, query is the plain title."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Breaking Bad", year=2008)
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["aniworld"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        req = _make_request(content_type="series", season=1, episode=5)
        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(req)

        mock_plugin.isolated_search.assert_awaited_once_with(
            "Breaking Bad", 5000, season=1, episode=5
        )

    async def test_multiple_plugins_combined(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr1 = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/1"}],
        )
        sr2 = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://filemoon.sx/e/2"}],
        )

        plugin_a = AsyncMock()
        plugin_a.search = AsyncMock(return_value=[sr1])
        del plugin_a.scraping
        plugin_a.isolated_search = plugin_a.search
        plugin_b = AsyncMock()
        plugin_b.search = AsyncMock(return_value=[sr2])
        del plugin_b.scraping
        plugin_b.isolated_search = plugin_b.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/ok"}],
        )

        good_plugin = AsyncMock()
        good_plugin.search = AsyncMock(return_value=[sr])
        del good_plugin.scraping
        good_plugin.isolated_search = good_plugin.search
        bad_plugin = AsyncMock()
        bad_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))
        del bad_plugin.scraping
        bad_plugin.isolated_search = bad_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["missing"] if p == "stream" else []
        )
        plugins.get.side_effect = KeyError("not found")

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_empty_search_results_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_source_plugin_tagged_on_results(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/both"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[
                {"url": "https://streamtape.com/v/low", "quality": "SD"},
                {"url": "https://voe.sx/e/high", "quality": "1080p"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        # Should have 2 streams (different hosters, order depends on sorter)
        assert len(result) == 2

    async def test_plugin_without_search_method_skipped(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        # Plugin without search method
        no_search_plugin = MagicMock(spec=[])

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
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

    async def test_slow_plugin_cancelled_by_timeout(self) -> None:
        """A plugin exceeding plugin_timeout_seconds is cancelled."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/fast"}],
        )

        fast_plugin = AsyncMock()
        fast_plugin.search = AsyncMock(return_value=[sr])
        del fast_plugin.scraping
        fast_plugin.isolated_search = fast_plugin.search

        async def _slow_search(*_a: object, **_kw: object) -> list[SearchResult]:
            await asyncio.sleep(10)
            return []

        slow_plugin = AsyncMock()
        slow_plugin.search = AsyncMock(side_effect=_slow_search)
        del slow_plugin.scraping
        slow_plugin.isolated_search = slow_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["fast", "slow"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda name: {
            "fast": fast_plugin,
            "slow": slow_plugin,
        }[name]

        config = _make_config(plugin_timeout_seconds=0.1)
        uc = _make_use_case(
            tmdb=tmdb, plugins=plugins, search_engine=engine, config=config
        )
        result = await uc.execute(_make_request())

        # Fast plugin result should be present, slow plugin timed out
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/fast"


# ---------------------------------------------------------------------------
# Title-match filtering
# ---------------------------------------------------------------------------


class TestTitleMatchFiltering:
    async def test_wrong_titles_filtered(self) -> None:
        """Only results matching the reference title pass through."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr_good = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/good"}],
        )
        sr_sequel = _make_search_result(
            title="Iron Man 2",
            download_links=[{"url": "https://voe.sx/e/sequel"}],
        )
        sr_unrelated = _make_search_result(
            title="Avengers Endgame",
            download_links=[{"url": "https://voe.sx/e/unrelated"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr_good, sr_sequel, sr_unrelated])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        urls = {s.url for s in result}
        assert "https://voe.sx/e/good" in urls
        assert "https://voe.sx/e/sequel" not in urls
        assert "https://voe.sx/e/unrelated" not in urls

    async def test_all_filtered_returns_empty(self) -> None:
        """When all results are below threshold, return empty list."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Completely Unrelated Film",
            download_links=[{"url": "https://voe.sx/e/bad"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_no_year_still_filters_by_title(self) -> None:
        """Even without year info, title similarity is applied."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man")
        )

        sr_good = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/match"}],
        )
        sr_bad = _make_search_result(
            title="Spider Man",
            download_links=[{"url": "https://voe.sx/e/nomatch"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr_good, sr_bad])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        urls = {s.url for s in result}
        assert "https://voe.sx/e/match" in urls
        assert "https://voe.sx/e/nomatch" not in urls


# ---------------------------------------------------------------------------
# Stream link caching + proxy URLs
# ---------------------------------------------------------------------------


class TestStreamLinkProxy:
    async def test_proxy_urls_generated_with_base_url(self) -> None:
        """When stream_link_repo and base_url are provided, URLs are proxied."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
        )
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert result[0].url.startswith("http://localhost:8080/api/v1/stremio/play/")
        assert "voe.sx" not in result[0].url
        # The play link keeps the stream's hints (autoplay of the next episode)
        assert result[0].behavior_hints is not None
        assert result[0].behavior_hints["bingeGroup"].startswith("scavengarr|")
        repo.save.assert_awaited()

    async def test_no_proxy_without_base_url(self) -> None:
        """Without base_url, original hoster URLs are returned."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
        )
        # No base_url → no proxying
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"
        repo.save.assert_not_awaited()

    async def test_no_proxy_without_repo(self) -> None:
        """Without stream_link_repo, original hoster URLs are returned."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # No repo → no proxying
        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"


# ---------------------------------------------------------------------------
# Resolver echo-URL filtering (skip unplayable streams)
# ---------------------------------------------------------------------------


class TestResolverEchoFiltering:
    """Streams whose resolver only validates (echoes the URL) must be skipped."""

    async def test_echo_url_streams_skipped(self) -> None:
        """XFS-style resolver returns same URL → stream excluded from output."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        # One stream with an XFS embed URL (veev)
        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://veev.to/e/abc123456789", "hoster": "VEEV"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver echoes the URL back (XFS behaviour)
        async def _echo_resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_echo_resolve),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Stream should be skipped — not included in output
        assert len(result) == 0

    async def test_direct_video_url_streams_kept(self) -> None:
        """Resolver extracting a real video URL → stream included."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc123", "hoster": "VOE"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver extracts a real HLS video URL
        async def _real_resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(
                video_url="https://cdn.voe.sx/hls/master.m3u8",
                is_hls=True,
                headers={"Referer": "https://voe.sx/"},
            )

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_real_resolve),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert _video(uc, result[0]) == "https://cdn.voe.sx/hls/master.m3u8"

    async def test_mixed_streams_only_playable_kept(self) -> None:
        """Mix of echo and real resolvers → only playable streams in output."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://veev.to/e/abc123456789", "hoster": "VEEV"},
                {"url": "https://voe.sx/e/def456", "hoster": "VOE"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # VOE extracts real URL, Veev echoes
        async def _mixed_resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "voe.sx" in url:
                return ResolvedStream(
                    video_url="https://cdn.voe.sx/hls/master.m3u8",
                    is_hls=True,
                    headers={"Referer": "https://voe.sx/"},
                )
            return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_mixed_resolve),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Only the VOE stream should remain
        assert len(result) == 1
        assert _video(uc, result[0]).endswith("master.m3u8")

    async def test_unresolved_streams_dropped_when_resolver_configured(self) -> None:
        """Streams that fail resolution (None) are dropped to avoid 502 proxy."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://unknown-hoster.com/v/abc", "hoster": "UNKNOWN"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver returns None (no resolver found for hoster)
        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(return_value=None),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Unresolvable streams are dropped — the /play/ proxy would always 502
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Multi-language search dispatch
# ---------------------------------------------------------------------------


class TestMultiLanguageDispatch:
    """Tests for multi-language search dispatch in execute()."""

    @staticmethod
    def _make_plugins_mock(
        names: list[str],
        plugin_languages: dict[str, list[str]],
        mock_plugin: AsyncMock,
    ) -> MagicMock:
        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: names if p == "stream" else []
        plugins.get.return_value = mock_plugin
        plugins.get_languages.side_effect = lambda n: plugin_languages.get(n, ["de"])
        return plugins

    async def test_german_only_plugin_uses_german_queries(self) -> None:
        """Plugin with languages=["de"] searches with German title."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            side_effect=lambda imdb_id, language="de": (
                TitleMatchInfo(title="Der Pate", year=1972)
                if language == "de"
                else TitleMatchInfo(title="The Godfather", year=1972)
            )
        )

        sr = _make_search_result(
            title="Der Pate",
            download_links=[{"url": "https://voe.sx/e/pate"}],
        )
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = self._make_plugins_mock(
            ["de-plugin"], {"de-plugin": ["de"]}, mock_plugin
        )

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        # Should have been called with German language
        tmdb.get_title_and_year.assert_any_await("tt1234567", language="de")
        # Should NOT have been called with English
        en_calls = [
            c
            for c in tmdb.get_title_and_year.call_args_list
            if c.kwargs.get("language") == "en"
        ]
        assert len(en_calls) == 0

    async def test_english_plugin_uses_english_queries(self) -> None:
        """Plugin with languages=["en"] searches with English title."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            side_effect=lambda imdb_id, language="de": (
                TitleMatchInfo(title="The Godfather", year=1972)
                if language == "en"
                else TitleMatchInfo(title="Der Pate", year=1972)
            )
        )

        sr = _make_search_result(
            title="The Godfather",
            download_links=[{"url": "https://voe.sx/e/godfather"}],
        )
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = self._make_plugins_mock(
            ["en-plugin"], {"en-plugin": ["en"]}, mock_plugin
        )

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        # Should have fetched English title
        tmdb.get_title_and_year.assert_any_await("tt1234567", language="en")

    async def test_bilingual_plugin_gets_both_queries(self) -> None:
        """Plugin with languages=["de", "en"] gets queries in both."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            side_effect=lambda imdb_id, language="de": (
                TitleMatchInfo(title="Der Pate", year=1972)
                if language == "de"
                else TitleMatchInfo(title="The Godfather", year=1972)
            )
        )

        sr = _make_search_result(
            title="Der Pate",
            download_links=[{"url": "https://voe.sx/e/pate"}],
        )
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = self._make_plugins_mock(
            ["both-plugin"],
            {"both-plugin": ["de", "en"]},
            mock_plugin,
        )

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        # Should have fetched both languages
        tmdb.get_title_and_year.assert_any_await("tt1234567", language="de")
        tmdb.get_title_and_year.assert_any_await("tt1234567", language="en")
        # Plugin should have been searched with queries from both languages
        search_calls = mock_plugin.search.call_args_list
        all_queries = {c.args[0] for c in search_calls}
        assert "Der Pate" in all_queries
        assert "The Godfather" in all_queries

    async def test_mixed_plugins_grouped_by_language(self) -> None:
        """Plugins with different languages are searched independently."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            side_effect=lambda imdb_id, language="de": (
                TitleMatchInfo(title="Der Pate", year=1972)
                if language == "de"
                else TitleMatchInfo(title="The Godfather", year=1972)
            )
        )

        sr = _make_search_result(
            title="Der Pate",
            download_links=[{"url": "https://voe.sx/e/pate"}],
        )
        de_plugin = AsyncMock()
        de_plugin.search = AsyncMock(return_value=[sr])
        del de_plugin.scraping
        de_plugin.isolated_search = de_plugin.search

        sr_en = _make_search_result(
            title="The Godfather",
            download_link="https://filemoon.sx/e/godfather",
            download_links=[{"url": "https://filemoon.sx/e/godfather"}],
        )
        en_plugin = AsyncMock()
        en_plugin.search = AsyncMock(return_value=[sr_en])
        del en_plugin.scraping
        en_plugin.isolated_search = en_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["de-site", "en-site"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda n: {
            "de-site": de_plugin,
            "en-site": en_plugin,
        }[n]
        plugins.get_languages.side_effect = lambda n: {
            "de-site": ["de"],
            "en-site": ["en"],
        }[n]

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 2
        urls = {s.url for s in result}
        assert "https://voe.sx/e/pate" in urls
        assert "https://filemoon.sx/e/godfather" in urls


# ---------------------------------------------------------------------------
# Browser warmup (pre-warming shared Playwright browser)
# ---------------------------------------------------------------------------


class TestBrowserWarmup:
    """Tests for the fire-and-forget browser pre-warm in PluginSearchRunner."""

    @pytest.mark.asyncio
    async def test_warmup_fn_called_when_configured(self) -> None:
        """Browser warmup fn fires when configured, regardless of plugin mode."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Test", year=2024)
        )

        sr = _make_search_result()
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin
        plugins.get_languages.return_value = ["de"]
        plugins.get_mode.return_value = "httpx"

        warmup_fn = AsyncMock(return_value=(MagicMock(), MagicMock()))
        uc = StremioStreamUseCase(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            config=_make_config(),
            sorter=StreamSorter(_make_config()),
            convert_fn=convert_search_results,
            filter_fn=filter_by_title_match,
            episode_filter_fn=filter_by_episode,
            user_agent=DEFAULT_USER_AGENT,
            max_results_var=search_max_results,
            browser_warmup_fn=warmup_fn,
            pool=ConcurrencyPool(httpx_slots=100, pw_slots=100),
        )

        await uc.execute(_make_request())

        warmup_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warmup_fn_not_called_when_not_configured(self) -> None:
        """When no warmup fn is provided, no crash occurs."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Test", year=2024)
        )

        sr = _make_search_result()
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin
        plugins.get_languages.return_value = ["de"]

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)

        # Should not crash when browser_warmup_fn is None
        result = await uc.execute(_make_request())
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_pw_semaphore_dynamic_sizing(self) -> None:
        """PW semaphore is capped at the actual PW plugin count."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Test", year=2024)
        )

        sr = _make_search_result()
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        # 2 PW + 1 httpx plugins
        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["pw1", "pw2", "httpx1"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin
        plugins.get_languages.return_value = ["de"]
        plugins.get_mode.side_effect = lambda n: (
            "playwright" if n.startswith("pw") else "httpx"
        )

        config = _make_config(max_concurrent_playwright=10)
        uc = StremioStreamUseCase(
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
            pool=ConcurrencyPool(httpx_slots=100, pw_slots=100),
        )

        result = await uc.execute(_make_request())
        # All 3 plugins searched — dynamic semaphore doesn't block
        assert plugins.get.call_count >= 3
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Resolve phase: dedup after resolution, overall deadline
# ---------------------------------------------------------------------------


def _resolving_use_case(
    links: list[dict[str, str]],
    resolve: object,
    config: StremioConfig | None = None,
) -> StremioStreamUseCase:
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    srs = [
        _make_search_result(
            title="Iron Man", release_name=link.pop("release"), download_links=[link]
        )
        for link in links
    ]
    mock_plugin = AsyncMock()
    mock_plugin.search = AsyncMock(return_value=srs)
    del mock_plugin.scraping
    mock_plugin.isolated_search = mock_plugin.search
    engine = AsyncMock()
    engine.validate_results = AsyncMock(side_effect=lambda r: r)
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: ["hdfilme"] if p == "stream" else []
    plugins.get.return_value = mock_plugin
    return _make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        search_engine=engine,
        config=config,
        stream_link_repo=AsyncMock(),
        resolve_fn=AsyncMock(side_effect=resolve),
    )


def _stream_id(url: str) -> str:
    path = urlparse(url).path
    if "/play/" in path:
        return path.rsplit("/play/", 1)[1]
    return path.split("/proxy/", 1)[1].split("/", 1)[0]


def _video(uc: StremioStreamUseCase, stream: StremioStream) -> str:
    """The video URL behind *stream*: /play and the HLS proxy serve the
    stored link's."""
    repo = uc._stream_link_repo
    assert isinstance(repo, AsyncMock)
    saved = {c.args[0].stream_id: c.args[0] for c in repo.save.await_args_list}
    return saved[_stream_id(stream.url)].video_url


def _resolved(url: str) -> ResolvedStream:
    return ResolvedStream(
        video_url=f"https://cdn.example/{url.rsplit('/', 1)[-1]}.mp4",
        headers={"Referer": "https://voe.sx/"},
    )


_BEST = {
    "url": "https://voe.sx/e/best",
    "hoster": "VOE",
    "release": "Iron.Man.2008.German.1080p.BluRay",
}
_SECOND = {
    "url": "https://voe.sx/e/second",
    "hoster": "VOE",
    "release": "Iron.Man.2008.German.720p.WEB",
}
_SLOW = {
    "url": "https://streamtape.com/e/slow",
    "hoster": "Streamtape",
    "release": "Iron.Man.2008.German.1080p.BluRay",
}
_FAST = dict(_SECOND, url="https://voe.sx/e/fast")


class TestResolvePhase:
    async def test_next_stream_of_a_hoster_replaces_a_failed_one(self) -> None:
        """Dedup happens after resolution: a failing best VOE stream must not
        take the working second VOE stream with it."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            return None if url.endswith("/best") else _resolved(url)

        uc = _resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [_video(uc, s) for s in result] == ["https://cdn.example/second.mp4"]

    async def test_one_stream_per_hoster(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return _resolved(url)

        uc = _resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [_video(uc, s) for s in result] == ["https://cdn.example/best.mp4"]

    async def test_one_stream_per_hoster_and_language(self) -> None:
        """Dub and sub on the same hoster are different content (anime)."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return _resolved(url)

        sub = {
            "url": "https://voe.sx/e/sub",
            "hoster": "VOE",
            "release": "Iron.Man.2008.GERMAN.SUBBED.720p.WEB",
        }
        uc = _resolving_use_case([dict(_BEST), dict(_SECOND), sub], _resolve)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [_video(uc, s) for s in result] == [
            "https://cdn.example/best.mp4",
            "https://cdn.example/sub.mp4",
        ]

    async def test_further_streams_of_a_resolved_hoster_are_not_resolved(
        self,
    ) -> None:
        """Resolving every candidate at once opens dozens of connections to
        distinct CDNs within a second, which home routers block as a port
        scan; a hoster's next stream is only tried after its best failed."""
        calls: list[str] = []

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            calls.append(url)
            return _resolved(url)

        uc = _resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert calls == [_BEST["url"]]

    async def test_deadline_returns_what_is_resolved(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            if "slow" in url:
                await asyncio.sleep(10)
            return _resolved(url)

        uc = _resolving_use_case(
            [dict(_SLOW), dict(_FAST)],
            _resolve,
            config=_make_config(stream_deadline_seconds=0.2),
        )

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert loop.time() - start < 2
        assert [_video(uc, s) for s in result] == ["https://cdn.example/fast.mp4"]

    def test_answer_policy_defaults(self) -> None:
        """5 streams or everything done, at most 60 s, plugins 30 s
        (maintainer's decision after the fifth round)."""
        config = _make_config()
        assert config.resolve_target_count == 5
        assert config.stream_deadline_seconds == 60.0
        assert config.plugin_timeout_seconds == 30.0

    async def test_the_answer_goes_out_at_the_target(self) -> None:
        """Titles with many streams waited for the soft deadline and the
        grace (11 s) although 5 streams were there after about 5 s."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "slow" in url:
                await asyncio.sleep(10)
            return _resolved(url)

        uc = _resolving_use_case(
            [dict(_SLOW), dict(_FAST)],
            _resolve,
            config=_make_config(resolve_target_count=1),
        )

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert loop.time() - start < 2
        assert [_video(uc, s) for s in result] == ["https://cdn.example/fast.mp4"]

    async def test_below_the_target_the_answer_waits_for_every_resolution(
        self,
    ) -> None:
        """A title whose only other hoster is slow keeps it."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "slow" in url:
                await asyncio.sleep(0.3)
            return _resolved(url)

        uc = _resolving_use_case([dict(_SLOW), dict(_FAST)], _resolve)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert sorted(_video(uc, s) for s in result) == [
            "https://cdn.example/fast.mp4",
            "https://cdn.example/slow.mp4",
        ]


class TestStreamLinkSaveFailures:
    """A failed stream-link save drops only the streams that need the link."""

    @staticmethod
    def _use_case(
        repo: AsyncMock, resolve_fn: AsyncMock | None = None
    ) -> StremioStreamUseCase:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )
        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc", "hoster": "VOE"},
                {"url": "https://streamtape.com/v/xyz", "hoster": "Streamtape"},
            ],
        )
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping
        mock_plugin.isolated_search = mock_plugin.search
        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin
        return _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=resolve_fn,
        )

    async def test_play_stream_without_saved_link_is_dropped(self) -> None:
        async def _save(link: CachedStreamLink) -> None:
            if "voe" in link.hoster_url:
                raise RuntimeError("cache down")

        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=_save)

        result = await self._use_case(repo).execute(
            _make_request(), base_url="http://localhost:8080"
        )

        assert len(result) == 1
        assert result[0].url.startswith("http://localhost:8080/api/v1/stremio/play/")

    async def test_only_the_answered_streams_save_a_link(self) -> None:
        """Saving a link per ranked stream (73 for one film) delayed the
        answer by 6 s; each answered stream needs one, behind /play/ or the
        proxy."""
        repo = AsyncMock()

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            if "voe" in url:
                return ResolvedStream(
                    video_url="https://cdn.voe.example/hls/master.m3u8",
                    headers={"Referer": "https://voe.sx/"},
                    is_hls=True,
                )
            return None

        result = await self._use_case(repo, AsyncMock(side_effect=_resolve)).execute(
            _make_request(), base_url="http://localhost:8080"
        )

        assert len(result) == 1
        saved = [c.args[0].hoster_url for c in repo.save.await_args_list]
        assert saved == ["https://voe.sx/e/abc"]

    async def test_a_stream_whose_link_is_not_saved_is_dropped(self) -> None:
        """/play/ and the proxy would not find it."""

        async def _save(link: CachedStreamLink) -> None:
            if "voe" in link.hoster_url:
                raise RuntimeError("cache down")

        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=_save)

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(
                video_url=f"{url}.mp4".replace("https://", "https://cdn.")
            )

        uc = self._use_case(repo, AsyncMock(side_effect=_resolve))
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [_video(uc, s) for s in result] == [
            "https://cdn.streamtape.com/v/xyz.mp4"
        ]

    async def test_a_link_keeps_its_id_in_every_answer(self) -> None:
        """Stremio keeps the stream object (Continue Watching): its link is
        the same one, refreshed by each answer."""
        repo = AsyncMock()

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(video_url=f"{url}.mp4")

        uc = self._use_case(repo, AsyncMock(side_effect=_resolve))
        first = await uc.execute(_make_request(), base_url="http://localhost:8080")
        second = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert sorted(s.url for s in first) == sorted(s.url for s in second)


# ---------------------------------------------------------------------------
# Search cache, single-flight, late plugins, early answer
# ---------------------------------------------------------------------------

_KEY = "stremio:search:tt1234567:None:None"
_TTL = 1800


def _memory_cache() -> AsyncMock:
    """CachePort keeping its entries in ``cache.data``."""
    data: dict[str, object] = {}
    cache = AsyncMock()
    cache.data = data
    cache.get = AsyncMock(side_effect=lambda key: data.get(key))
    cache.set = AsyncMock(
        side_effect=lambda key, value, *, ttl=None: data.__setitem__(key, value)
    )
    return cache


def _hit(link: str, title: str = "Iron Man") -> SearchResult:
    """A search result with one hoster link (one stream per hoster)."""
    return _make_search_result(
        title=title,
        download_link=link,
        download_links=[{"url": link, "quality": "1080p"}],
    )


def _site(
    results: list[SearchResult],
    delay: float = 0.0,
    *,
    cancelled: asyncio.Event | None = None,
) -> AsyncMock:
    """Plugin answering with *results* after *delay* seconds."""

    async def _search(*_args: object, **_kwargs: object) -> list[SearchResult]:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            if cancelled is not None:
                cancelled.set()
            raise
        return results

    plugin = AsyncMock()
    del plugin.scraping
    plugin.isolated_search = AsyncMock(side_effect=_search)
    return plugin


def _cached_use_case(
    sites: dict[str, AsyncMock],
    cache: AsyncMock,
    *,
    ttl: int = _TTL,
    hard: float = 1.0,
    telemetry: TelemetryPort = NO_TELEMETRY,
) -> StremioStreamUseCase:
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: (
        sorted(sites) if p == "stream" else []
    )
    plugins.get.side_effect = sites.__getitem__
    return _make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=_make_config(
            plugin_timeout_seconds=hard,
            stream_deadline_seconds=hard + 1.0,
        ),
        cache=cache,
        search_ttl_seconds=ttl,
        telemetry=telemetry,
    )


async def _eventually(check: Callable[[], bool], timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.01)


def _links(cache: AsyncMock) -> list[str]:
    entry = cache.data.get(_KEY)
    return sorted(r.download_link for r in entry.results) if entry else []


class TestSearchCache:
    """Stremio search results are cached per title (stale-while-revalidate,
    single-flight); slow plugins run on into the cache, and the answer goes
    out at the soft deadline when there are results."""

    async def test_miss_stores_the_matching_results(self) -> None:
        cache = _memory_cache()
        site = _site([_hit("https://voe.sx/e/1"), _hit("https://x.to/2", "Rugrats")])
        uc = _cached_use_case({"a": site}, cache)

        streams = await uc.execute(_make_request())

        assert [s.url for s in streams] == ["https://voe.sx/e/1"]
        assert _links(cache) == ["https://voe.sx/e/1"]
        assert cache.data[_KEY].total == 2
        assert cache.set.await_args.kwargs["ttl"] == _TTL + STALE_SECONDS

    async def test_fresh_hit_skips_the_search(self) -> None:
        cache = _memory_cache()
        site = _site([_hit("https://voe.sx/e/1")])
        uc = _cached_use_case({"a": site}, cache)

        first = await uc.execute(_make_request())
        second = await uc.execute(_make_request())

        assert [s.url for s in second] == [s.url for s in first]
        assert site.isolated_search.await_count == 1

    async def test_stale_hit_answers_and_refreshes_in_the_background(self) -> None:
        cache = _memory_cache()
        cache.data[_KEY] = CachedSearch(
            results=[_hit("https://voe.sx/e/old")],
            total=1,
            stored_at=time.time() - _TTL - 1,
        )
        site = _site([_hit("https://voe.sx/e/new")])
        uc = _cached_use_case({"a": site}, cache)

        streams = await uc.execute(_make_request())

        assert [s.url for s in streams] == ["https://voe.sx/e/old"]
        await _eventually(lambda: _links(cache) == ["https://voe.sx/e/new"])
        assert time.time() - cache.data[_KEY].stored_at < _TTL
        assert site.isolated_search.await_count == 1

    async def test_concurrent_requests_share_one_search(self) -> None:
        cache = _memory_cache()
        site = _site([_hit("https://voe.sx/e/1")], delay=0.05)
        uc = _cached_use_case({"a": site}, cache)

        first, second = await asyncio.gather(
            uc.execute(_make_request()), uc.execute(_make_request())
        )

        assert [s.url for s in first] == ["https://voe.sx/e/1"]
        assert [s.url for s in second] == ["https://voe.sx/e/1"]
        assert site.isolated_search.await_count == 1

    async def test_without_a_resolver_the_answer_waits_for_the_search(
        self,
    ) -> None:
        cache = _memory_cache()
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "slow": _site([_hit("https://dood.to/e/slow")], delay=0.3),
        }
        uc = _cached_use_case(sites, cache)

        streams = await uc.execute(_make_request())

        assert sorted(s.url for s in streams) == [
            "https://dood.to/e/slow",
            "https://voe.sx/e/fast",
        ]
        assert _links(cache) == ["https://dood.to/e/slow", "https://voe.sx/e/fast"]

    async def test_a_plugin_past_the_timeout_is_cancelled(self) -> None:
        cache = _memory_cache()
        cancelled = asyncio.Event()
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "stuck": _site([], delay=30, cancelled=cancelled),
        }
        uc = _cached_use_case(sites, cache, hard=0.2)

        await uc.execute(_make_request())

        await asyncio.wait_for(cancelled.wait(), 2.0)
        assert _links(cache) == ["https://voe.sx/e/fast"]

    async def test_aclose_cancels_a_running_search(self) -> None:
        """The request waiting on it answers with what the search found."""
        cancelled = asyncio.Event()
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "stuck": _site([], delay=30, cancelled=cancelled),
        }
        uc = _cached_use_case(sites, _memory_cache(), hard=10.0)
        request = asyncio.create_task(uc.execute(_make_request()))
        await asyncio.sleep(0.1)

        await uc.aclose()

        assert cancelled.is_set()
        assert [s.url for s in await request] == ["https://voe.sx/e/fast"]

    async def test_cache_off_answers_with_every_plugin(self) -> None:
        cache = _memory_cache()
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "slow": _site([_hit("https://dood.to/e/slow")], delay=0.2),
        }
        uc = _cached_use_case(sites, cache, ttl=0)

        streams = await uc.execute(_make_request())

        assert sorted(s.url for s in streams) == [
            "https://dood.to/e/slow",
            "https://voe.sx/e/fast",
        ]
        cache.get.assert_not_awaited()
        cache.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cached answers: a search from the cache answers with cached resolutions
# ---------------------------------------------------------------------------


class _Resolutions:
    """Resolver registry stand-in: resolve() caches, cached() peeks."""

    def __init__(
        self,
        *,
        alive: tuple[str, ...] = (),
        dead: tuple[str, ...] = (),
        delay: float = 0.0,
    ) -> None:
        self.store: dict[str, ResolvedStream | None] = {u: _resolved(u) for u in alive}
        self.store.update(dict.fromkeys(dead))
        self.delay = delay
        self.calls: list[str] = []
        self.cancelled = asyncio.Event()

    async def resolve(self, url: str, hoster: str = "") -> ResolvedStream | None:
        if url in self.store:
            return self.store[url]
        self.calls.append(url)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.store[url] = _resolved(url)
        return self.store[url]

    def cached(self, url: str) -> tuple[bool, ResolvedStream | None]:
        return url in self.store, self.store.get(url)


_VOE = "https://voe.sx/e/best"
_VOE_2 = "https://voe.sx/e/second"
_DOOD = "https://dood.to/e/new"


def _from_cache(
    links: list[dict[str, str]],
    resolutions: _Resolutions,
    *,
    cached: bool = True,
    telemetry: TelemetryPort = NO_TELEMETRY,
) -> StremioStreamUseCase:
    """Use case whose search for the title is in the cache (or, with
    *cached* False, comes from a plugin)."""
    results = [
        _make_search_result(
            title="Iron Man", release_name=link.pop("release"), download_links=[link]
        )
        for link in links
    ]
    cache = _memory_cache()
    if cached:
        cache.data[_KEY] = CachedSearch(
            results=results, total=len(results), stored_at=time.time()
        )
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: ["a"] if p == "stream" else []
    plugins.get.return_value = _site(results)
    return _make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=_make_config(),
        stream_link_repo=AsyncMock(),
        resolve_fn=resolutions.resolve,
        cached_resolution_fn=resolutions.cached,
        cache=cache,
        search_ttl_seconds=_TTL,
        telemetry=telemetry,
    )


def _link(url: str, release: str = "Iron.Man.2008.German.1080p.BluRay") -> dict:
    return {"url": url, "hoster": url.split("/")[2].split(".")[0], "release": release}


class TestCachedAnswers:
    """A cached answer waited for the resolve grace (4.1-4.4 s) when one of
    its links had no cached resolution; it goes out at once now, and the
    other links resolve in the background for the next request."""

    async def test_answers_at_once_with_the_cached_streams(self) -> None:
        resolutions = _Resolutions(alive=(_VOE,), delay=0.5)
        uc = _from_cache([_link(_VOE), _link(_DOOD)], resolutions)

        started = time.monotonic()
        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert time.monotonic() - started < 0.3
        assert [_video(uc, s) for s in streams] == ["https://cdn.example/best.mp4"]
        await _eventually(lambda: _DOOD in resolutions.store)
        assert resolutions.calls == [_DOOD]

    async def test_a_link_cached_as_dead_gives_way_to_the_hosters_next(
        self,
    ) -> None:
        resolutions = _Resolutions(alive=(_VOE_2,), dead=(_VOE,))
        uc = _from_cache(
            [_link(_VOE), _link(_VOE_2, "Iron.Man.2008.German.720p.WEB")],
            resolutions,
        )

        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [_video(uc, s) for s in streams] == ["https://cdn.example/second.mp4"]
        assert resolutions.calls == []

    async def test_without_a_cached_stream_the_answer_waits_as_before(self) -> None:
        resolutions = _Resolutions(dead=(_VOE,), delay=0.1)
        uc = _from_cache([_link(_VOE), _link(_DOOD)], resolutions)

        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [_video(uc, s) for s in streams] == ["https://cdn.example/new.mp4"]
        assert resolutions.calls == [_DOOD]

    async def test_a_new_search_waits_for_its_links(self) -> None:
        """Only an answer from the search cache goes out early."""
        resolutions = _Resolutions(alive=(_VOE,), delay=0.1)
        uc = _from_cache([_link(_VOE), _link(_DOOD)], resolutions, cached=False)

        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert sorted(_video(uc, s) for s in streams) == [
            "https://cdn.example/best.mp4",
            "https://cdn.example/new.mp4",
        ]

    async def test_one_background_resolution_per_title(self) -> None:
        resolutions = _Resolutions(alive=(_VOE,), delay=0.3)
        uc = _from_cache([_link(_VOE), _link(_DOOD)], resolutions)

        await uc.execute(_make_request(), base_url="http://localhost:8080")
        await uc.execute(_make_request(), base_url="http://localhost:8080")

        await _eventually(lambda: _DOOD in resolutions.store)
        assert resolutions.calls == [_DOOD]

    async def test_aclose_ends_the_background_resolution(self) -> None:
        resolutions = _Resolutions(alive=(_VOE,), delay=30)
        uc = _from_cache([_link(_VOE), _link(_DOOD)], resolutions)
        await uc.execute(_make_request(), base_url="http://localhost:8080")
        await _eventually(lambda: resolutions.calls == [_DOOD])

        await uc.aclose()

        assert resolutions.cancelled.is_set()


def _answering_use_case(
    sites: dict[str, AsyncMock],
    cache: AsyncMock,
    resolutions: _Resolutions,
    telemetry: TelemetryPort = NO_TELEMETRY,
    **config: object,
) -> StremioStreamUseCase:
    """Use case that searches *sites* and resolves with *resolutions*."""
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: (
        sorted(sites) if p == "stream" else []
    )
    plugins.get.side_effect = sites.__getitem__
    return _make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        config=_make_config(**config),
        stream_link_repo=AsyncMock(),
        resolve_fn=resolutions.resolve,
        cached_resolution_fn=resolutions.cached,
        cache=cache,
        search_ttl_seconds=_TTL,
        telemetry=telemetry,
    )


class TestAnswerPolicy:
    """The answer goes out at 5 streams, when the search and every
    resolution are done, at the latest at the deadline; it no longer waits
    for the soft deadline (7 s) and the grace (4 s)."""

    async def test_at_the_target_while_a_slow_plugin_still_searches(
        self,
    ) -> None:
        cache = _memory_cache()
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "slow": _site([_hit("https://dood.to/e/slow")], delay=0.5),
        }
        uc = _answering_use_case(sites, cache, _Resolutions(), resolve_target_count=1)

        started = time.monotonic()
        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert time.monotonic() - started < 0.4
        assert [_video(uc, s) for s in streams] == ["https://cdn.example/fast.mp4"]
        # The slow plugin goes on and its results reach the cache
        await _eventually(lambda: len(_links(cache)) == 2)

    async def test_links_resolve_while_the_search_runs(self) -> None:
        """The fast plugin's link is resolved before the slow one answers."""
        resolutions = _Resolutions(delay=0.05)
        slow_done = asyncio.Event()

        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await asyncio.sleep(0.3)
            slow_done.set()
            return [_hit("https://dood.to/e/slow")]

        slow = _site([])
        slow.isolated_search = AsyncMock(side_effect=_slow)
        sites = {"fast": _site([_hit("https://voe.sx/e/fast")]), "slow": slow}
        uc = _answering_use_case(sites, _memory_cache(), resolutions)
        request = asyncio.create_task(
            uc.execute(_make_request(), base_url="http://localhost:8080")
        )

        await _eventually(lambda: "https://voe.sx/e/fast" in resolutions.store)
        assert not slow_done.is_set()
        streams = await request

        assert sorted(_video(uc, s) for s in streams) == [
            "https://cdn.example/fast.mp4",
            "https://cdn.example/slow.mp4",
        ]

    async def test_when_everything_is_done_below_the_target(self) -> None:
        sites = {
            "a": _site([_hit("https://voe.sx/e/1")]),
            "b": _site([_hit("https://dood.to/e/2")], delay=0.2),
        }
        uc = _answering_use_case(sites, _memory_cache(), _Resolutions())

        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert sorted(_video(uc, s) for s in streams) == [
            "https://cdn.example/1.mp4",
            "https://cdn.example/2.mp4",
        ]

    async def test_at_the_deadline_with_what_is_resolved(self) -> None:
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "stuck": _site([], delay=30),
        }
        uc = _answering_use_case(
            sites,
            _memory_cache(),
            _Resolutions(),
            plugin_timeout_seconds=10.0,
            stream_deadline_seconds=0.3,
        )

        started = time.monotonic()
        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert 0.25 < time.monotonic() - started < 1.5
        assert [_video(uc, s) for s in streams] == ["https://cdn.example/fast.mp4"]
        await uc.aclose()

    async def test_requests_on_one_search_both_answer_at_the_target(self) -> None:
        cache = _memory_cache()
        fast = _site([_hit("https://voe.sx/e/fast")])
        sites = {"fast": fast, "slow": _site([], delay=0.5)}
        uc = _answering_use_case(sites, cache, _Resolutions(), resolve_target_count=1)

        started = time.monotonic()
        first, second = await asyncio.gather(
            uc.execute(_make_request(), base_url="http://localhost:8080"),
            uc.execute(_make_request(), base_url="http://localhost:8080"),
        )

        assert time.monotonic() - started < 0.4
        assert [_video(uc, s) for s in first] == ["https://cdn.example/fast.mp4"]
        assert [_video(uc, s) for s in second] == ["https://cdn.example/fast.mp4"]
        assert fast.isolated_search.await_count == 1
        await uc.aclose()


# ---------------------------------------------------------------------------
# Telemetry: requests, phases and answers are recorded in the use case
# ---------------------------------------------------------------------------


def _value(t: Telemetry, name: str, **labels: str) -> float | None:
    return t.registry.get_sample_value(name, labels)


def _requests(t: Telemetry, source: str, outcome: str) -> float | None:
    return _value(t, "scavengarr_stremio_request_total", source=source, outcome=outcome)


def _phases(t: Telemetry, phase: str, outcome: str) -> float | None:
    return _value(t, "scavengarr_stremio_phase_total", phase=phase, outcome=outcome)


def _one_stream_plugin(tmdb: AsyncMock, telemetry: Telemetry) -> StremioStreamUseCase:
    plugins = MagicMock()
    plugins.get_languages.return_value = ["de"]
    plugins.get_by_provides.side_effect = lambda p: ["a"] if p == "stream" else []
    return _make_use_case(tmdb=tmdb, plugins=plugins, telemetry=telemetry)


class TestTelemetry:
    """Each request records its search state, phases, end reason and streams."""

    @pytest.fixture
    def t(self) -> Telemetry:
        return Telemetry()

    async def test_a_new_search(self, t: Telemetry) -> None:
        sites = {
            "a": _site([_hit("https://voe.sx/e/1")]),
            "b": _site([_hit("https://dood.to/e/2")]),
        }
        uc = _answering_use_case(sites, _memory_cache(), _Resolutions(), telemetry=t)

        streams = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(streams) == 2
        assert _requests(t, "search", "streams") == 1
        seconds = "scavengarr_stremio_request_seconds_count"
        assert _value(t, seconds, source="search") == 1
        assert _phases(t, "metadata", "found") == 1
        assert _phases(t, "resolve", "done") == 1
        await _eventually(lambda: _phases(t, "search", "ok") == 1)
        assert _value(t, "scavengarr_stremio_streams_bucket", le="2.0") == 1
        assert _value(t, "scavengarr_stremio_streams_bucket", le="1.0") == 0

    async def test_answer_at_the_target(self, t: Telemetry) -> None:
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "slow": _site([_hit("https://dood.to/e/slow")], delay=0.5),
        }
        uc = _answering_use_case(
            sites, _memory_cache(), _Resolutions(), telemetry=t, resolve_target_count=1
        )

        await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert _phases(t, "resolve", "target") == 1
        await uc.aclose()

    async def test_answer_at_the_deadline(self, t: Telemetry) -> None:
        sites = {
            "fast": _site([_hit("https://voe.sx/e/fast")]),
            "stuck": _site([], delay=30),
        }
        uc = _answering_use_case(
            sites,
            _memory_cache(),
            _Resolutions(),
            telemetry=t,
            plugin_timeout_seconds=10.0,
            stream_deadline_seconds=0.3,
        )

        await uc.execute(_make_request(), base_url="http://localhost:8080")
        await uc.aclose()

        assert _phases(t, "resolve", "deadline") == 1
        # Shutdown cut the search and its stuck plugin
        assert _phases(t, "search", "cut") == 1
        stuck = {"plugin": "stuck", "outcome": "cut"}
        assert _value(t, "scavengarr_plugin_search_total", **stuck) == 1

    async def test_fresh_cache_entry(self, t: Telemetry) -> None:
        site = _site([_hit("https://voe.sx/e/1")])
        uc = _cached_use_case({"a": site}, _memory_cache(), telemetry=t)

        await uc.execute(_make_request())
        await uc.execute(_make_request())

        assert _requests(t, "search", "streams") == 1
        assert _requests(t, "cache", "streams") == 1

    async def test_stale_cache_entry(self, t: Telemetry) -> None:
        cache = _memory_cache()
        cache.data[_KEY] = CachedSearch(
            results=[_hit("https://voe.sx/e/old")],
            total=1,
            stored_at=time.time() - _TTL - 1,
        )
        site = _site([_hit("https://voe.sx/e/new")])
        uc = _cached_use_case({"a": site}, cache, telemetry=t)

        await uc.execute(_make_request())

        assert _requests(t, "stale", "streams") == 1
        await _eventually(lambda: _phases(t, "search", "ok") == 1)

    async def test_a_request_joining_a_running_search(self, t: Telemetry) -> None:
        site = _site([_hit("https://voe.sx/e/1")], delay=0.05)
        uc = _cached_use_case({"a": site}, _memory_cache(), telemetry=t)

        await asyncio.gather(uc.execute(_make_request()), uc.execute(_make_request()))

        assert _requests(t, "search", "streams") == 1
        assert _requests(t, "joined", "streams") == 1
        await _eventually(lambda: _phases(t, "search", "ok") == 1)
        seconds = "scavengarr_stremio_phase_seconds_count"
        assert _value(t, seconds, phase="search") == 1

    async def test_a_cached_answer_counts_without_a_duration(
        self, t: Telemetry
    ) -> None:
        resolutions = _Resolutions(alive=(_VOE,), delay=0.05)
        uc = _from_cache([_link(_VOE), _link(_DOOD)], resolutions, telemetry=t)

        await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert _requests(t, "cache", "streams") == 1
        assert _phases(t, "resolve", "cached") == 1
        seconds = "scavengarr_stremio_phase_seconds_count"
        assert _value(t, seconds, phase="resolve") is None
        await _eventually(lambda: _phases(t, "background_resolve", "done") == 1)

    async def test_title_not_found(self, t: Telemetry) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=None)
        uc = _one_stream_plugin(tmdb, t)

        assert await uc.execute(_make_request()) == []

        assert _requests(t, "none", "no_title") == 1
        assert _phases(t, "metadata", "not_found") == 1
        assert _value(t, "scavengarr_stremio_streams_bucket", le="0.0") == 1

    async def test_no_stream_plugins(self, t: Telemetry) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []
        uc = _make_use_case(plugins=plugins, telemetry=t)

        assert await uc.execute(_make_request()) == []

        assert _requests(t, "none", "no_plugins") == 1

    async def test_nothing_found(self, t: Telemetry) -> None:
        uc = _cached_use_case({"a": _site([])}, _memory_cache(), telemetry=t)

        assert await uc.execute(_make_request()) == []

        assert _requests(t, "search", "empty") == 1

    async def test_a_failing_request_is_an_error(self, t: Telemetry) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(side_effect=RuntimeError("tmdb down"))
        uc = _one_stream_plugin(tmdb, t)

        with pytest.raises(RuntimeError):
            await uc.execute(_make_request())

        assert _requests(t, "none", "error") == 1
        assert _phases(t, "metadata", "error") == 1

    async def test_with_tracing_a_request_is_one_trace(self) -> None:
        exporter = InMemorySpanExporter()
        t = Telemetry(tracing=Tracing(SimpleSpanProcessor(exporter)))
        sites = {"a": _site([_hit("https://voe.sx/e/1")])}
        uc = _answering_use_case(sites, _memory_cache(), _Resolutions(), telemetry=t)

        await uc.execute(_make_request(), base_url="http://localhost:8080")
        await uc.aclose()

        spans = exporter.get_finished_spans()
        assert {s.name for s in spans} >= {
            "stremio_request",
            "stremio_phase metadata",
            "stremio_phase search",
            "plugin_search a",
            "stremio_phase resolve",
        }
        assert len({s.context.trace_id for s in spans if s.context}) == 1
