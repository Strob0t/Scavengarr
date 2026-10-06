"""Tests for the title resolution of Stremio stream requests.

The title match and the titles per language (``TitleResolver``) are tested
through the whole use case, as before they left it; the worker-thread test
drives the resolver itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult

from .stremio_support import (
    make_config,
    make_request,
    make_search_result,
    make_use_case,
)

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

        sr_good = make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/good"}],
        )
        sr_sequel = make_search_result(
            title="Iron Man 2",
            download_links=[{"url": "https://voe.sx/e/sequel"}],
        )
        sr_unrelated = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())
        assert result == []

    async def test_no_year_still_filters_by_title(self) -> None:
        """Even without year info, title similarity is applied."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man")
        )

        sr_good = make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/match"}],
        )
        sr_bad = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

        urls = {s.url for s in result}
        assert "https://voe.sx/e/match" in urls
        assert "https://voe.sx/e/nomatch" not in urls


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

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        sr = make_search_result(
            title="Der Pate",
            download_links=[{"url": "https://voe.sx/e/pate"}],
        )
        de_plugin = AsyncMock()
        de_plugin.search = AsyncMock(return_value=[sr])
        del de_plugin.scraping
        de_plugin.isolated_search = de_plugin.search

        sr_en = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

        assert len(result) >= 2
        urls = {s.url for s in result}
        assert "https://voe.sx/e/pate" in urls
        assert "https://filemoon.sx/e/godfather" in urls


class TestWorkerThread:
    """The title filter runs in a worker thread; its log lines
    (``title_match_summary``) keep the request's ``request_id``."""

    @pytest.mark.asyncio
    async def test_the_title_filter_keeps_the_log_context(self) -> None:
        seen: dict[str, object] = {}

        def _filter(results: list[SearchResult], *_args: object, **_kw: object):
            seen.update(structlog.contextvars.get_contextvars())
            return results

        titles = TitleResolver(
            tmdb=AsyncMock(),
            plugins=MagicMock(),
            filter_fn=_filter,
            config=make_config(),
        )
        with structlog.contextvars.bound_contextvars(request_id="r1"):
            await titles.matching(
                [make_search_result()], TitleMatchInfo(title="Iron Man")
            )

        assert seen["request_id"] == "r1"
