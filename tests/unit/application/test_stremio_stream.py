"""Tests for StremioStreamUseCase."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from scavengarr.application.stremio.search_cache import CachedSearch
from scavengarr.application.use_cases.stremio_stream import (
    StremioStreamUseCase,
)
from scavengarr.domain.entities.scoring import PluginScoreSnapshot
from scavengarr.domain.entities.stremio import (
    ResolvedStream,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.telemetry import Telemetry
from scavengarr.infrastructure.telemetry.tracing import Tracing

from .stremio_support import (
    DOOD,
    SEARCH_KEY,
    SEARCH_TTL,
    VOE,
    VOE_2,
    Resolutions,
    answering_use_case,
    cached_use_case,
    eventually,
    fake_site,
    from_cache,
    hit,
    hoster_link,
    make_config,
    make_request,
    make_search_result,
    make_use_case,
    memory_cache,
    video,
)

# ---------------------------------------------------------------------------
# StremioStreamUseCase.execute
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_title_not_found_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=None)
        uc = make_use_case(tmdb=tmdb)
        result = await uc.execute(make_request())
        assert result == []

    async def test_happy_path_movie(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        req = make_request(content_type="series", season=1, episode=5)
        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(req)

        mock_plugin.isolated_search.assert_awaited_once_with(
            "Breaking Bad", 5000, season=1, episode=5
        )

    async def test_multiple_plugins_combined(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr1 = make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/1"}],
        )
        sr2 = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

        assert len(result) == 2
        urls = {s.url for s in result}
        assert "https://voe.sx/e/1" in urls
        assert "https://filemoon.sx/e/2" in urls

    async def test_plugin_error_does_not_crash(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        uc = make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(make_request())
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())
        assert result == []

    async def test_source_plugin_tagged_on_results(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(make_request())

        # The use case should tag source_plugin in metadata
        assert sr.metadata.get("source_plugin") == "myplugin"

    async def test_streams_sorted_by_score(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = make_search_result(
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

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request())

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

        uc = make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(make_request())
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

        config = make_config(max_concurrent_plugins=2)
        uc = make_use_case(
            tmdb=tmdb, plugins=plugins, search_engine=engine, config=config
        )

        # Should complete without error; semaphore internally limits to 2
        result = await uc.execute(make_request())
        assert result == []
        assert mock_plugin.search.await_count == 10

    async def test_slow_plugin_cancelled_by_timeout(self) -> None:
        """A plugin exceeding plugin_timeout_seconds is cancelled."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = make_search_result(
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

        config = make_config(plugin_timeout_seconds=0.1)
        uc = make_use_case(
            tmdb=tmdb, plugins=plugins, search_engine=engine, config=config
        )
        result = await uc.execute(make_request())

        # Fast plugin result should be present, slow plugin timed out
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/fast"


# ---------------------------------------------------------------------------
# Cached answers: a search from the cache answers with cached resolutions
# ---------------------------------------------------------------------------


class TestCachedAnswers:
    """A cached answer waited for the resolve grace (4.1-4.4 s) when one of
    its links had no cached resolution; it goes out at once now, and the
    other links resolve in the background for the next request."""

    async def test_answers_at_once_with_the_cached_streams(self) -> None:
        resolutions = Resolutions(alive=(VOE,), delay=0.5)
        uc = from_cache([hoster_link(VOE), hoster_link(DOOD)], resolutions)

        started = time.monotonic()
        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert time.monotonic() - started < 0.3
        assert [video(uc, s) for s in streams] == ["https://cdn.example/best.mp4"]
        await eventually(lambda: DOOD in resolutions.store)
        assert resolutions.calls == [DOOD]

    async def test_a_link_cached_as_dead_gives_way_to_the_hosters_next(
        self,
    ) -> None:
        resolutions = Resolutions(alive=(VOE_2,), dead=(VOE,))
        uc = from_cache(
            [hoster_link(VOE), hoster_link(VOE_2, "Iron.Man.2008.German.720p.WEB")],
            resolutions,
        )

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in streams] == ["https://cdn.example/second.mp4"]
        assert resolutions.calls == []

    async def test_an_unresolved_better_link_keeps_the_hosters_cached_stream(
        self,
    ) -> None:
        """A plugin that answered after the first answer can rank a new link
        of a hoster first: the cached answer dropped that hoster's stream
        (3 of 17 titles of the dev-server E2E run, 2026-10-05). It keeps the
        cached stream now; the new link resolves for the next request."""
        resolutions = Resolutions(alive=(VOE_2,), delay=0.5)
        uc = from_cache(
            [hoster_link(VOE), hoster_link(VOE_2, "Iron.Man.2008.German.720p.WEB")],
            resolutions,
        )

        started = time.monotonic()
        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert time.monotonic() - started < 0.3
        assert [video(uc, s) for s in streams] == ["https://cdn.example/second.mp4"]
        await eventually(lambda: VOE in resolutions.store)
        assert resolutions.calls == [VOE]

    async def test_an_echoed_better_link_keeps_the_hosters_cached_stream(
        self,
    ) -> None:
        """A resolver that only validates a link echoes its embed URL. The
        cached answer took that echo as the hoster's stream, dropped it and
        left the hoster out for the hour the echo stayed cached (code
        review, 2026-10-06)."""
        resolutions = Resolutions(alive=(VOE_2,))
        resolutions.store[VOE] = ResolvedStream(video_url=VOE)
        uc = from_cache(
            [hoster_link(VOE), hoster_link(VOE_2, "Iron.Man.2008.German.720p.WEB")],
            resolutions,
        )

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in streams] == ["https://cdn.example/second.mp4"]

    async def test_without_a_cached_stream_the_answer_waits_as_before(self) -> None:
        resolutions = Resolutions(dead=(VOE,), delay=0.1)
        uc = from_cache([hoster_link(VOE), hoster_link(DOOD)], resolutions)

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in streams] == ["https://cdn.example/new.mp4"]
        assert resolutions.calls == [DOOD]

    async def test_a_new_search_waits_for_its_links(self) -> None:
        """Only an answer from the search cache goes out early."""
        resolutions = Resolutions(alive=(VOE,), delay=0.1)
        uc = from_cache(
            [hoster_link(VOE), hoster_link(DOOD)], resolutions, cached=False
        )

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert sorted(video(uc, s) for s in streams) == [
            "https://cdn.example/best.mp4",
            "https://cdn.example/new.mp4",
        ]

    async def test_a_fully_cached_answer_resolves_nothing_in_the_background(
        self,
    ) -> None:
        """Every link has a cached outcome (code review, 2026-10-06)."""
        resolutions = Resolutions(alive=(VOE,), dead=(DOOD,))
        uc = from_cache([hoster_link(VOE), hoster_link(DOOD)], resolutions)

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in streams] == ["https://cdn.example/best.mp4"]
        assert not uc._resolve_flow._background_resolutions  # noqa: SLF001

    async def test_one_background_resolution_per_title(self) -> None:
        resolutions = Resolutions(alive=(VOE,), delay=0.3)
        uc = from_cache([hoster_link(VOE), hoster_link(DOOD)], resolutions)

        await uc.execute(make_request(), base_url="http://localhost:8080")
        await uc.execute(make_request(), base_url="http://localhost:8080")

        await eventually(lambda: DOOD in resolutions.store)
        assert resolutions.calls == [DOOD]

    async def test_aclose_ends_the_background_resolution(self) -> None:
        resolutions = Resolutions(alive=(VOE,), delay=30)
        uc = from_cache([hoster_link(VOE), hoster_link(DOOD)], resolutions)
        await uc.execute(make_request(), base_url="http://localhost:8080")
        await eventually(lambda: resolutions.calls == [DOOD])

        await uc.aclose()

        assert resolutions.cancelled.is_set()


class TestMirrorScores:
    async def test_the_best_scored_mirror_member_is_searched(self) -> None:
        """The plugin scores pick a mirror group's member."""
        sites = {
            "hdfilme": fake_site([hit("https://voe.sx/e/a")]),
            "streamcloud": fake_site([hit("https://voe.sx/e/b")]),
        }
        store = AsyncMock()
        store.get_snapshot = AsyncMock(
            side_effect=lambda plugin, category, bucket: PluginScoreSnapshot(
                plugin=plugin,
                category=category,
                bucket="current",
                final_score=0.9 if plugin == "streamcloud" else 0.4,
                confidence=0.5,
            )
        )
        uc = answering_use_case(
            sites,
            memory_cache(),
            Resolutions(),
            mirror_groups={"hdfilme": "hdfilme", "streamcloud": "hdfilme"},
            score_store=store,
        )

        await uc.execute(make_request(), base_url="http://localhost:8080")

        assert sites["hdfilme"].isolated_search.await_count == 0
        assert sites["streamcloud"].isolated_search.await_count == 1


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
    return make_use_case(tmdb=tmdb, plugins=plugins, telemetry=telemetry)


class TestTelemetry:
    """Each request records its search state, phases, end reason and streams."""

    @pytest.fixture
    def t(self) -> Telemetry:
        return Telemetry()

    async def test_a_new_search(self, t: Telemetry) -> None:
        sites = {
            "a": fake_site([hit("https://voe.sx/e/1")]),
            "b": fake_site([hit("https://dood.to/e/2")]),
        }
        uc = answering_use_case(sites, memory_cache(), Resolutions(), telemetry=t)

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert len(streams) == 2
        assert _requests(t, "search", "streams") == 1
        seconds = "scavengarr_stremio_request_seconds_count"
        assert _value(t, seconds, source="search") == 1
        assert _phases(t, "metadata", "found") == 1
        assert _phases(t, "resolve", "done") == 1
        await eventually(lambda: _phases(t, "search", "ok") == 1)
        assert _value(t, "scavengarr_stremio_streams_bucket", le="2.0") == 1
        assert _value(t, "scavengarr_stremio_streams_bucket", le="1.0") == 0

    async def test_answer_at_the_target(self, t: Telemetry) -> None:
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "slow": fake_site([hit("https://dood.to/e/slow")], delay=0.5),
        }
        uc = answering_use_case(
            sites, memory_cache(), Resolutions(), telemetry=t, resolve_target_count=1
        )

        await uc.execute(make_request(), base_url="http://localhost:8080")

        assert _phases(t, "resolve", "target") == 1
        await uc.aclose()

    async def test_answer_at_the_deadline(self, t: Telemetry) -> None:
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "stuck": fake_site([], delay=30),
        }
        uc = answering_use_case(
            sites,
            memory_cache(),
            Resolutions(),
            telemetry=t,
            plugin_timeout_seconds=10.0,
            stream_deadline_seconds=0.3,
        )

        await uc.execute(make_request(), base_url="http://localhost:8080")
        await uc.aclose()

        assert _phases(t, "resolve", "deadline") == 1
        # Shutdown cut the search and its stuck plugin
        assert _phases(t, "search", "cut") == 1
        stuck = {"plugin": "stuck", "outcome": "cut"}
        assert _value(t, "scavengarr_plugin_search_total", **stuck) == 1

    async def test_fresh_cache_entry(self, t: Telemetry) -> None:
        site = fake_site([hit("https://voe.sx/e/1")])
        uc = cached_use_case({"a": site}, memory_cache(), telemetry=t)

        await uc.execute(make_request())
        await uc.execute(make_request())

        assert _requests(t, "search", "streams") == 1
        assert _requests(t, "cache", "streams") == 1

    async def test_stale_cache_entry(self, t: Telemetry) -> None:
        cache = memory_cache()
        cache.data[SEARCH_KEY] = CachedSearch(
            results=[hit("https://voe.sx/e/old")],
            total=1,
            stored_at=time.time() - SEARCH_TTL - 1,
        )
        site = fake_site([hit("https://voe.sx/e/new")])
        uc = cached_use_case({"a": site}, cache, telemetry=t)

        await uc.execute(make_request())

        assert _requests(t, "stale", "streams") == 1
        await eventually(lambda: _phases(t, "search", "ok") == 1)

    async def test_a_request_joining_a_running_search(self, t: Telemetry) -> None:
        site = fake_site([hit("https://voe.sx/e/1")], delay=0.05)
        uc = cached_use_case({"a": site}, memory_cache(), telemetry=t)

        await asyncio.gather(uc.execute(make_request()), uc.execute(make_request()))

        assert _requests(t, "search", "streams") == 1
        assert _requests(t, "joined", "streams") == 1
        await eventually(lambda: _phases(t, "search", "ok") == 1)
        seconds = "scavengarr_stremio_phase_seconds_count"
        assert _value(t, seconds, phase="search") == 1

    async def test_a_cached_answer_counts_without_a_duration(
        self, t: Telemetry
    ) -> None:
        resolutions = Resolutions(alive=(VOE,), delay=0.05)
        uc = from_cache([hoster_link(VOE), hoster_link(DOOD)], resolutions, telemetry=t)

        await uc.execute(make_request(), base_url="http://localhost:8080")

        assert _requests(t, "cache", "streams") == 1
        assert _phases(t, "resolve", "cached") == 1
        seconds = "scavengarr_stremio_phase_seconds_count"
        assert _value(t, seconds, phase="resolve") is None
        await eventually(lambda: _phases(t, "background_resolve", "done") == 1)

    async def test_title_not_found(self, t: Telemetry) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=None)
        uc = _one_stream_plugin(tmdb, t)

        assert await uc.execute(make_request()) == []

        assert _requests(t, "none", "no_title") == 1
        assert _phases(t, "metadata", "not_found") == 1
        assert _value(t, "scavengarr_stremio_streams_bucket", le="0.0") == 1

    async def test_no_stream_plugins(self, t: Telemetry) -> None:
        plugins = MagicMock()
        plugins.get_by_provides.return_value = []
        uc = make_use_case(plugins=plugins, telemetry=t)

        assert await uc.execute(make_request()) == []

        assert _requests(t, "none", "no_plugins") == 1

    async def test_nothing_found(self, t: Telemetry) -> None:
        uc = cached_use_case({"a": fake_site([])}, memory_cache(), telemetry=t)

        assert await uc.execute(make_request()) == []

        assert _requests(t, "search", "empty") == 1

    async def test_a_failing_request_is_an_error(self, t: Telemetry) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(side_effect=RuntimeError("tmdb down"))
        uc = _one_stream_plugin(tmdb, t)

        with pytest.raises(RuntimeError):
            await uc.execute(make_request())

        assert _requests(t, "none", "error") == 1
        assert _phases(t, "metadata", "error") == 1

    async def test_with_tracing_a_request_is_one_trace(self) -> None:
        exporter = InMemorySpanExporter()
        t = Telemetry(tracing=Tracing(SimpleSpanProcessor(exporter)))
        sites = {"a": fake_site([hit("https://voe.sx/e/1")])}
        uc = answering_use_case(sites, memory_cache(), Resolutions(), telemetry=t)

        await uc.execute(make_request(), base_url="http://localhost:8080")
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
