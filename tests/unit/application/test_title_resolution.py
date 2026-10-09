"""Tests for the title resolution of Stremio stream requests.

The title match and the titles per language (``TitleResolver``) are tested
through the whole use case, as before they left it; the default languages
and the worker-thread test drive the resolver itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import (
    EpisodeMeta,
    EpisodeRef,
    SeriesMeta,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.telemetry import NO_TELEMETRY, TelemetryPort
from scavengarr.infrastructure.telemetry import Telemetry

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
        de_plugin.isolated_search = de_plugin.search

        sr_en = make_search_result(
            title="The Godfather",
            download_link="https://filemoon.sx/e/godfather",
            download_links=[{"url": "https://filemoon.sx/e/godfather"}],
        )
        en_plugin = AsyncMock()
        en_plugin.search = AsyncMock(return_value=[sr_en])
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


class TestDefaultLanguages:
    """A plugin's first language is the language of its results that name
    none (the ranking's language score)."""

    def test_each_plugin_gets_its_first_language(self) -> None:
        plugins = MagicMock()
        plugins.get_languages.side_effect = {
            "a": ["de", "en"],
            "b": [],
            "c": ["en"],
        }.__getitem__
        titles = TitleResolver(
            tmdb=AsyncMock(),
            plugins=plugins,
            filter_fn=MagicMock(),
            config=make_config(),
        )

        assert titles.default_languages(["a", "b", "c"]) == {"a": "de", "c": "en"}


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


# ---------------------------------------------------------------------------
# Series identity: the catalog record gives every reference its imdb_id
# and whether it is animation; the stage records the lookup's outcome
# ---------------------------------------------------------------------------

_LABOON = "The First Line of Defense? The Giant Whale Laboon Appears!"


def _meta(
    genres: tuple[str, ...] = (), episodes: tuple[EpisodeMeta, ...] = ()
) -> SeriesMeta:
    return SeriesMeta(name="One Piece", year=1999, genres=genres, episodes=episodes)


def _resolver(
    lookup: tuple[SeriesMeta | None, str], telemetry: TelemetryPort = NO_TELEMETRY
) -> tuple[TitleResolver, AsyncMock]:
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        side_effect=lambda imdb_id, *, language: TitleMatchInfo(
            title="One Piece" if language == "en" else "One Piece DE", year=1999
        )
    )
    tmdb.get_title_by_tmdb_id = AsyncMock(return_value="Some Show")
    series_meta = AsyncMock()
    series_meta.lookup = AsyncMock(return_value=lookup)
    titles = TitleResolver(
        tmdb=tmdb,
        plugins=MagicMock(),
        filter_fn=MagicMock(),
        config=make_config(),
        series_meta=series_meta,
        telemetry=telemetry,
    )
    return titles, series_meta


def _series(imdb_id: str = "tt0388629") -> StremioStreamRequest:
    return make_request(imdb_id=imdb_id, content_type="series", season=5, episode=2)


class TestSeriesIdentity:
    @pytest.mark.asyncio
    async def test_an_animated_series(self) -> None:
        titles, series_meta = _resolver(
            (_meta(("Animation", "Action", "Adventure")), "found")
        )

        infos, meta = await titles.title_infos(_series(), ["de", "en"])

        assert meta is not None
        assert infos["de"] == TitleMatchInfo(
            title="One Piece DE",
            year=1999,
            content_type="series",
            imdb_id="tt0388629",
            animation=True,
        )
        assert infos["en"] is not None
        assert infos["en"].title == "One Piece"
        assert infos["en"].animation is True
        # One catalog lookup, shared by the languages
        series_meta.lookup.assert_awaited_once_with("series", "tt0388629")

    @pytest.mark.asyncio
    async def test_a_live_action_series(self) -> None:
        titles, _ = _resolver((_meta(("Action", "Adventure", "Comedy")), "found"))

        infos, _record = await titles.title_infos(_series("tt11737520"), ["de"])

        assert infos["de"] is not None
        assert infos["de"].animation is False
        assert infos["de"].imdb_id == "tt11737520"

    @pytest.mark.asyncio
    async def test_without_a_record_the_kind_is_unknown(self) -> None:
        titles, _ = _resolver((None, "not_found"))

        infos, meta = await titles.title_infos(_series(), ["de"])

        assert meta is None
        assert infos["de"] is not None
        assert infos["de"].animation is None
        assert infos["de"].imdb_id == "tt0388629"

    @pytest.mark.asyncio
    async def test_an_unknown_title_stays_unknown(self) -> None:
        titles, _ = _resolver((_meta(("Animation",)), "found"))
        titles._tmdb.get_title_and_year = AsyncMock(return_value=None)

        infos, meta = await titles.title_infos(_series(), ["de"])

        assert meta is not None
        assert infos == {"de": None}

    @pytest.mark.asyncio
    async def test_a_tmdb_id_asks_the_catalog_nothing(self) -> None:
        titles, series_meta = _resolver((_meta(("Animation",)), "found"))

        infos, meta = await titles.title_infos(_series("tmdb:37854"), ["de"])

        assert meta is None
        assert infos["de"] == TitleMatchInfo(title="Some Show", content_type="series")
        series_meta.lookup.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("lookup", "outcome"),
        [
            ((_meta(("Animation",)), "found"), "found"),
            ((None, "not_found"), "not_found"),
            ((None, "error"), "error"),
        ],
    )
    async def test_the_stage_records_the_outcome(
        self, lookup: tuple[SeriesMeta | None, str], outcome: str
    ) -> None:
        telemetry = Telemetry()
        titles, _ = _resolver(lookup, telemetry)

        infos, _record = await titles.title_infos(_series(), ["de"])

        # An error leaves the kind unknown and the request goes on
        assert infos["de"] is not None
        assert (
            telemetry.registry.get_sample_value(
                "scavengarr_stremio_phase_total",
                {"phase": "series_meta", "outcome": outcome},
            )
            == 1
        )


# ---------------------------------------------------------------------------
# The episode reference: the catalog entry's title and date, and the
# absolute number as the position among the regular seasons
# ---------------------------------------------------------------------------


def _one_piece() -> SeriesMeta:
    """Cinemeta's One Piece shape (2026-10-09): seasons 1 to 4 list 8, 22,
    17 and 13 episodes, so S5E2 is the 62nd regular episode."""
    episodes = [
        EpisodeMeta(0, 1, "One Piece: Defeat the Pirate Ganzack! (OVA 1)", "1998-07-26")
    ]
    for season, count in ((1, 8), (2, 22), (3, 17), (4, 13), (5, 9)):
        episodes.extend(
            EpisodeMeta(season, number, f"S{season}E{number}", None)
            for number in range(1, count + 1)
        )
    episodes[-8] = EpisodeMeta(5, 2, _LABOON, "2001-03-21")
    return _meta(("Animation",), tuple(episodes))


class TestEpisodeRef:
    def test_s5e2_is_the_62nd_regular_episode(self) -> None:
        ref = TitleResolver.episode_ref(_series(), _one_piece())

        assert ref == EpisodeRef(
            season=5, episode=2, title=_LABOON, aired="2001-03-21", absolute=62
        )

    def test_the_order_of_the_list_does_not_count(self) -> None:
        meta = _one_piece()
        shuffled = SeriesMeta(
            meta.name, meta.year, meta.genres, tuple(reversed(meta.episodes))
        )

        ref = TitleResolver.episode_ref(_series(), shuffled)

        assert ref is not None
        assert ref.absolute == 62

    @pytest.mark.parametrize("given", [57, 61, 63, 67])
    def test_a_kitsu_number_near_the_position_wins(self, given: int) -> None:
        # A Kitsu entry that spans the series counts as the sites do, one
        # off at times (One Piece: 1089 where the list says 1088)
        ref = TitleResolver.episode_ref(_series(), _one_piece(), absolute=given)

        assert ref == EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=given)

    @pytest.mark.parametrize("given", [2, 56, 68, 1089])
    def test_a_season_entrys_number_gives_way_to_the_position(self, given: int) -> None:
        # Kitsu has an entry per season for most anime: its episode 2 of
        # the fifth season is no absolute number
        ref = TitleResolver.episode_ref(_series(), _one_piece(), absolute=given)

        assert ref == EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=62)

    def test_a_special_has_no_absolute_number(self) -> None:
        request = make_request(
            imdb_id="tt0388629", content_type="series", season=0, episode=1
        )

        ref = TitleResolver.episode_ref(request, _one_piece())

        assert ref == EpisodeRef(
            0, 1, "One Piece: Defeat the Pirate Ganzack! (OVA 1)", "1998-07-26", None
        )

    def test_an_episode_the_list_lacks_has_no_reference(self) -> None:
        """The newest episode of a running series, which the catalog lists
        with a delay: nothing to locate by, so the plugins answer as today."""
        request = make_request(
            imdb_id="tt0388629", content_type="series", season=22, episode=90
        )

        assert TitleResolver.episode_ref(request, _one_piece()) is None

    def test_a_kitsu_number_names_an_episode_the_list_lacks(self) -> None:
        request = make_request(
            imdb_id="tt0388629", content_type="series", season=22, episode=90
        )

        ref = TitleResolver.episode_ref(request, _one_piece(), absolute=1090)

        assert ref == EpisodeRef(22, 90, None, None, absolute=1090)

    def test_without_a_record(self) -> None:
        assert TitleResolver.episode_ref(_series(), None) is None

    def test_a_movie_request(self) -> None:
        request = make_request(imdb_id="tt1375666", content_type="movie")

        assert TitleResolver.episode_ref(request, _meta()) is None
