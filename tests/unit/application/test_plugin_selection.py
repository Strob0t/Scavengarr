"""Tests for the plugins a Stremio request searches (``PluginSelector``)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from scavengarr.application.stremio.plugin_selection import PluginSelector
from scavengarr.domain.entities.scoring import PluginScoreSnapshot
from scavengarr.domain.entities.stremio import TitleMatchInfo

from .stremio_support import (
    Resolutions,
    answering_use_case,
    fake_site,
    hit,
    make_config,
    make_request,
    make_search_result,
    make_use_case,
    memory_cache,
)


class TestStreamPlugins:
    async def test_no_stream_plugins_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.return_value = []

        uc = make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(make_request())
        assert result == []

    async def test_both_provides_plugins_included(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/both"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

        assert len(result) == 1

    async def test_deduplication_of_plugin_names(self) -> None:
        """Plugin in both stream and both is searched once."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(make_request())

        # Should only search once despite appearing in both lists
        mock_plugin.search.assert_awaited_once()


class TestScoredSelection:
    async def test_a_failing_score_store_searches_every_plugin(self) -> None:
        """Unreadable scores count as none: the request asks every plugin,
        as on a cold start, instead of failing."""
        sites = {
            "one": fake_site([hit("https://voe.sx/e/a")]),
            "two": fake_site([hit("https://dood.to/e/b")]),
        }
        store = AsyncMock()
        store.get_snapshot = AsyncMock(side_effect=ConnectionError("redis down"))
        uc = answering_use_case(
            sites,
            memory_cache(),
            Resolutions(),
            score_store=store,
            scoring_enabled=True,
        )

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert streams
        assert sites["one"].isolated_search.await_count == 1
        assert sites["two"].isolated_search.await_count == 1

    async def test_the_best_scored_plugins_are_searched(self) -> None:
        selector = _selector(
            {"a": (0.9, 0.5), "b": (0.3, 0.5), "c": (0.7, 0.5)},
            max_plugins_scored=2,
            exploration_probability=0.0,
        )

        assert await selector.select(["a", "b", "c"], 2000) == ["a", "c"]

    async def test_too_few_confident_scores_search_every_plugin(self) -> None:
        """Cold start: fewer than half of the plugins have a confident score."""
        selector = _selector({"a": (0.9, 0.5)}, max_plugins_scored=1)

        assert await selector.select(["a", "b", "c"], 2000) == ["a", "b", "c"]

    async def test_the_exploration_slot_adds_a_confident_plugin(self) -> None:
        """Only a plugin past the top N with a confident score can explore."""
        selector = _selector(
            {"a": (0.9, 0.5), "b": (0.8, 0.5), "c": (0.2, 0.5), "d": (0.6, 0.05)},
            max_plugins_scored=2,
            exploration_probability=1.0,
        )

        assert await selector.select(["a", "b", "c", "d"], 2000) == ["a", "b", "c"]

    async def test_without_scoring_every_plugin_is_searched(self) -> None:
        selector = _selector({"a": (0.9, 0.5)}, scoring_enabled=False)

        assert await selector.select(["a", "b"], 2000) == ["a", "b"]


def _selector(
    scores: dict[str, tuple[float, float]], **config: object
) -> PluginSelector:
    """Selector over a score store with (final_score, confidence) per plugin."""
    store = AsyncMock()
    store.get_snapshot = AsyncMock(
        side_effect=lambda plugin, category, bucket: (
            PluginScoreSnapshot(
                plugin=plugin,
                category=category,
                bucket=bucket,
                final_score=scores[plugin][0],
                confidence=scores[plugin][1],
            )
            if plugin in scores
            else None
        )
    )
    return PluginSelector(
        plugins=MagicMock(),
        score_store=store,
        config=make_config(**{"scoring_enabled": True, **config}),
    )
