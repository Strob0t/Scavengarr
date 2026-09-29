"""Tests for StremioStreamUseCase."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.use_cases.stremio_stream import (
    StremioStreamUseCase,
)
from scavengarr.domain.entities.stremio import (
    ResolvedStream,
    StreamQuality,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
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
    probe_fn: AsyncMock | None = None,
    resolve_fn: AsyncMock | None = None,
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
        probe_fn=probe_fn,
        resolve_fn=resolve_fn,
        pool=ConcurrencyPool(httpx_slots=100, pw_slots=100),
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
        assert "Iron Man" in result[0].name
        assert "(2008)" in result[0].name
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
        assert "master.m3u8" in result[0].url

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
        assert "master.m3u8" in result[0].url

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
# Stream probe at /stream time
# ---------------------------------------------------------------------------


_PROBE_HOSTERS = [
    "voe.sx",
    "streamtape.com",
    "dood.re",
    "filemoon.sx",
    "mixdrop.ag",
    "vidmoly.me",
    "streamwish.com",
    "vidoza.net",
    "upstream.to",
    "wolfstream.tv",
]


def _probe_test_setup(
    *,
    result_count: int = 3,
    probe_fn: AsyncMock | None = None,
    config: StremioConfig | None = None,
) -> tuple[StremioStreamUseCase, AsyncMock]:
    """Build a use case with probe callback for testing.

    Returns (use_case, repo_mock). The use case has a TMDB mock returning
    "Iron Man" (2008), a single plugin returning *result_count* streams,
    and a stream_link_repo mock for proxy URL generation.

    Each stream uses a different hoster domain to survive per-hoster
    deduplication.
    """
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )

    srs = [
        _make_search_result(
            title="Iron Man",
            download_links=[
                {
                    "url": (
                        f"https://{_PROBE_HOSTERS[i % len(_PROBE_HOSTERS)]}/e/stream{i}"
                    )
                }
            ],
        )
        for i in range(result_count)
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

    repo = AsyncMock()
    uc = _make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        search_engine=engine,
        config=config or _make_config(),
        stream_link_repo=repo,
        probe_fn=probe_fn,
    )
    return uc, repo


class TestStreamProbe:
    async def test_dead_links_filtered_by_probe(self) -> None:
        """Probe kills index 1 → only 2 streams returned."""
        probe_fn = AsyncMock(return_value={0, 2})
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 2
        probe_fn.assert_awaited_once()
        assert repo.save.await_count == 2

    async def test_probe_disabled_skips_filtering(self) -> None:
        """probe_at_stream_time=False → no filtering, all streams pass."""
        probe_fn = AsyncMock(return_value={0})
        config = _make_config(probe_at_stream_time=False)
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn, config=config)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 3
        probe_fn.assert_not_awaited()

    async def test_probe_without_fn_skips_filtering(self) -> None:
        """No probe_fn → all streams pass through unfiltered."""
        uc, repo = _probe_test_setup(result_count=3, probe_fn=None)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 3

    async def test_max_probe_count_limits_probing(self) -> None:
        """5 streams, max_probe_count=2 → only first 2 probed, rest pass."""
        # Probe kills index 0, keeps index 1
        probe_fn = AsyncMock(return_value={1})
        config = _make_config(max_probe_count=2)
        uc, repo = _probe_test_setup(result_count=5, probe_fn=probe_fn, config=config)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Index 0 killed, index 1 alive, indices 2-4 unprobed (pass through)
        assert len(result) == 4
        # Verify probe was called with only 2 targets
        call_args = probe_fn.call_args[0][0]
        assert len(call_args) == 2

    async def test_all_dead_returns_empty(self) -> None:
        """All streams fail probe → empty result."""
        probe_fn = AsyncMock(return_value=set())
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 0
        repo.save.assert_not_awaited()

    async def test_probe_preserves_stream_order(self) -> None:
        """Alive streams keep original sort order."""
        # Keep indices 0 and 2 → first and third stream
        probe_fn = AsyncMock(return_value={0, 2})
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 2
        # Both should be proxy URLs (order preserved)
        assert all(
            s.url.startswith("http://localhost:8080/api/v1/stremio/play/")
            for s in result
        )


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


def _video(url: str) -> ResolvedStream:
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


class TestResolvePhase:
    async def test_next_stream_of_a_hoster_replaces_a_failed_one(self) -> None:
        """Dedup happens after resolution: a failing best VOE stream must not
        take the working second VOE stream with it."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            return None if url.endswith("/best") else _video(url)

        uc = _resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [s.url for s in result] == ["https://cdn.example/second.mp4"]

    async def test_one_stream_per_hoster(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return _video(url)

        uc = _resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert [s.url for s in result] == ["https://cdn.example/best.mp4"]

    async def test_further_streams_of_a_resolved_hoster_are_not_resolved(
        self,
    ) -> None:
        """Resolving every candidate at once opens dozens of connections to
        distinct CDNs within a second, which home routers block as a port
        scan; a hoster's next stream is only tried after its best failed."""
        calls: list[str] = []

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            calls.append(url)
            return _video(url)

        uc = _resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert calls == [_BEST["url"]]

    async def test_deadline_returns_what_is_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scavengarr.application.use_cases import stremio_stream as mod

        monkeypatch.setattr(mod, "_MIN_RESOLVE_WINDOW_S", 0.2)

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            if "slow" in url:
                await asyncio.sleep(10)
            return _video(url)

        slow = {
            "url": "https://streamtape.com/e/slow",
            "hoster": "Streamtape",
            "release": "Iron.Man.2008.German.1080p.BluRay",
        }
        fast = dict(_SECOND, url="https://voe.sx/e/fast")
        uc = _resolving_use_case(
            [slow, fast], _resolve, config=_make_config(stream_deadline_seconds=0.1)
        )

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert loop.time() - start < 2
        assert [s.url for s in result] == ["https://cdn.example/fast.mp4"]

    def test_deadline_default(self) -> None:
        assert _make_config().stream_deadline_seconds == 15.0
