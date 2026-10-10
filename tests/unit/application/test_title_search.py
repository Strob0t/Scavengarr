"""Tests for the plugin search of a Stremio request's title (``TitleSearch``).

The tests drive the whole use case through ``execute()`` (factories in
``stremio_support.py``).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.stremio.search_cache import STALE_SECONDS, CachedSearch
from scavengarr.application.stremio.title_search import answer_budget_s
from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.concurrency import ConcurrencyPool
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.episode_filter import filter_by_episode
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match

from .stremio_support import (
    SEARCH_KEY,
    SEARCH_TTL,
    cached_links,
    cached_use_case,
    eventually,
    fake_site,
    hit,
    make_config,
    make_request,
    make_search_result,
    make_use_case,
    memory_cache,
)


def _of(plugin: str, link: str) -> SearchResult:
    result = hit(link)
    result.metadata["source_plugin"] = plugin
    return result


class TestAnswerBudget:
    """The answer budget is ``stremio.answer_budget_seconds`` when set, the
    plugin timeout otherwise; a budget above the plugin timeout is the
    plugin timeout (no plugin runs longer)."""

    @pytest.mark.parametrize(
        ("setting", "expected"), [(None, 30.0), (10.0, 10.0), (45.0, 30.0)]
    )
    def test_the_budget_from_the_settings(
        self, setting: float | None, expected: float
    ) -> None:
        assert answer_budget_s(30.0, setting) == expected


class TestSearchCache:
    """Stremio search results are cached per title (stale-while-revalidate,
    single-flight); the search runs on into the cache after the answer went
    out, until the plugin timeout."""

    async def test_miss_stores_the_matching_results(self) -> None:
        cache = memory_cache()
        site = fake_site([hit("https://voe.sx/e/1"), hit("https://x.to/2", "Rugrats")])
        uc = cached_use_case({"a": site}, cache)

        streams = await uc.execute(make_request())

        assert [s.url for s in streams] == ["https://voe.sx/e/1"]
        assert cached_links(cache) == ["https://voe.sx/e/1"]
        assert cache.data[SEARCH_KEY].total == 2
        assert cache.set.await_args.kwargs["ttl"] == SEARCH_TTL + STALE_SECONDS

    async def test_fresh_hit_skips_the_search(self) -> None:
        cache = memory_cache()
        site = fake_site([hit("https://voe.sx/e/1")])
        uc = cached_use_case({"a": site}, cache)

        first = await uc.execute(make_request())
        second = await uc.execute(make_request())

        assert [s.url for s in second] == [s.url for s in first]
        assert site.isolated_search.await_count == 1

    async def test_stale_hit_answers_and_refreshes_in_the_background(self) -> None:
        cache = memory_cache()
        cache.data[SEARCH_KEY] = CachedSearch(
            results=[_of("a", "https://voe.sx/e/old")],
            total=1,
            stored_at=time.time() - SEARCH_TTL - 1,
        )
        site = fake_site([hit("https://voe.sx/e/new")])
        uc = cached_use_case({"a": site}, cache)

        streams = await uc.execute(make_request())

        assert [s.url for s in streams] == ["https://voe.sx/e/old"]
        await eventually(lambda: cached_links(cache) == ["https://voe.sx/e/new"])
        assert time.time() - cache.data[SEARCH_KEY].stored_at < SEARCH_TTL
        assert site.isolated_search.await_count == 1

    async def test_stale_hits_refresh_one_title_at_a_time(self) -> None:
        """Every stale title asked for started its refresh at once: the
        searches split the plugin slots (fair share), and a refresh cut
        short replaced its entry with a thinner one (code review,
        2026-10-06). A refresh that waited keeps its whole plugin time."""
        cache = memory_cache()
        keys = [
            f"stremio:search:v3:movie:{imdb_id}:None:None:-"
            for imdb_id in ("tt1234567", "tt7654321")
        ]
        for key in keys:
            cache.data[key] = CachedSearch(
                results=[_of("a", "https://voe.sx/e/old")],
                total=1,
                stored_at=time.time() - SEARCH_TTL - 1,
            )
        running = peak = 0

        async def _search(*_args: object, **_kwargs: object) -> list[SearchResult]:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(0.3)
            finally:
                running -= 1
            return [hit("https://voe.sx/e/new")]

        site = fake_site([])
        site.isolated_search.side_effect = _search
        # The second refresh starts after 0.3 s and ends after 0.6 s
        uc = cached_use_case({"a": site}, cache, hard=0.5)

        await asyncio.gather(
            uc.execute(make_request()),
            uc.execute(make_request(imdb_id="tt7654321")),
        )

        await eventually(
            lambda: all(
                cache.data[key].results[0].download_link == "https://voe.sx/e/new"
                for key in keys
            )
        )
        assert peak == 1

    async def test_concurrent_requests_share_one_search(self) -> None:
        cache = memory_cache()
        site = fake_site([hit("https://voe.sx/e/1")], delay=0.05)
        uc = cached_use_case({"a": site}, cache)

        first, second = await asyncio.gather(
            uc.execute(make_request()), uc.execute(make_request())
        )

        assert [s.url for s in first] == ["https://voe.sx/e/1"]
        assert [s.url for s in second] == ["https://voe.sx/e/1"]
        assert site.isolated_search.await_count == 1

    async def test_without_a_resolver_the_answer_waits_for_the_search(
        self,
    ) -> None:
        cache = memory_cache()
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "slow": fake_site([hit("https://dood.to/e/slow")], delay=0.3),
        }
        uc = cached_use_case(sites, cache)

        streams = await uc.execute(make_request())

        assert sorted(s.url for s in streams) == [
            "https://dood.to/e/slow",
            "https://voe.sx/e/fast",
        ]
        assert cached_links(cache) == [
            "https://dood.to/e/slow",
            "https://voe.sx/e/fast",
        ]

    async def test_the_entry_at_the_budget_names_the_plugins_to_come(self) -> None:
        """One slot: the second plugin starts once the first is done, after
        the answer budget, and finishes within its own timeout. The entry
        written at the budget names it missing; its end rewrites the entry,
        and the search's end writes nothing new."""
        cache = memory_cache()
        sites = {
            "first": fake_site([hit("https://voe.sx/e/first")], delay=0.3),
            "second": fake_site([hit("https://dood.to/e/second")], delay=0.2),
        }
        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        uc = cached_use_case(sites, cache, hard=0.4, pool=pool)

        await uc.execute(make_request())

        await eventually(lambda: len(cache.set.await_args_list) == 2)
        entries = [c.args[1] for c in cache.set.await_args_list]
        assert [e.missing for e in entries] == [("second",), ()]
        assert [len(e.results) for e in entries] == [1, 2]
        assert entries[0].stored_at == entries[1].stored_at

    async def test_the_answer_waits_for_the_budget_only(self) -> None:
        """The request answers at the budget with the results so far; the
        plugin still searching is not cancelled and its results reach the
        cache (continue-cut-searches)."""
        cache = memory_cache()
        cancelled = asyncio.Event()
        sites = {
            "first": fake_site([hit("https://voe.sx/e/first")], delay=0.3),
            "second": fake_site(
                [hit("https://dood.to/e/second")], delay=0.2, cancelled=cancelled
            ),
        }
        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        uc = cached_use_case(sites, cache, hard=0.4, pool=pool)

        started = time.monotonic()
        streams = await uc.execute(make_request())

        assert 0.3 <= time.monotonic() - started < 0.6
        assert [s.url for s in streams] == ["https://voe.sx/e/first"]
        await eventually(lambda: len(cached_links(cache)) == 2)
        assert not cancelled.is_set()

    async def test_a_retry_past_the_budget_does_not_wait(self) -> None:
        """A request finding the running search past its budget answers at
        once with what it found (source ``cache``)."""
        cache = memory_cache()
        sites = {
            "first": fake_site([hit("https://voe.sx/e/first")], delay=0.3),
            "second": fake_site([hit("https://dood.to/e/second")], delay=0.3),
        }
        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        uc = cached_use_case(sites, cache, hard=0.4, pool=pool)
        await uc.execute(make_request())

        started = time.monotonic()
        streams = await uc.execute(make_request())

        assert time.monotonic() - started < 0.1
        assert [s.url for s in streams] == ["https://voe.sx/e/first"]
        await eventually(lambda: len(cached_links(cache)) == 2)

    async def test_a_budget_below_the_plugin_timeout_answers_at_the_budget(
        self,
    ) -> None:
        """``stremio.answer_budget_seconds`` below the plugin timeout: the
        request answers at the budget with the fast plugin's result while
        the slow one runs on to its own timeout; the entry at the budget
        names it missing, the end entry is complete. (Nothing arrived by the
        budget: nothing is stored then, an empty entry equals a miss.)"""
        cache = memory_cache()
        sites = {
            "first": fake_site([hit("https://voe.sx/e/first")], delay=0.1),
            "second": fake_site([hit("https://dood.to/e/second")], delay=1.0),
        }
        uc = cached_use_case(sites, cache, hard=2.0, answer_budget=0.2)

        started = time.monotonic()
        streams = await uc.execute(make_request())

        assert 0.2 <= time.monotonic() - started < 0.9
        assert [s.url for s in streams] == ["https://voe.sx/e/first"]
        await eventually(lambda: len(cache.set.await_args_list) == 2, timeout=3.0)
        entries = [c.args[1] for c in cache.set.await_args_list]
        assert [(e.missing, len(e.results)) for e in entries] == [
            (("second",), 1),
            ((), 2),
        ]

    async def test_a_plugin_past_the_timeout_is_cancelled(self) -> None:
        cache = memory_cache()
        cancelled = asyncio.Event()
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "stuck": fake_site([], delay=30, cancelled=cancelled),
        }
        uc = cached_use_case(sites, cache, hard=0.2)

        await uc.execute(make_request())

        await asyncio.wait_for(cancelled.wait(), 2.0)
        assert cached_links(cache) == ["https://voe.sx/e/fast"]

    async def test_aclose_cancels_a_running_search(self) -> None:
        """The request waiting on it answers with what the search found."""
        cancelled = asyncio.Event()
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "stuck": fake_site([], delay=30, cancelled=cancelled),
        }
        uc = cached_use_case(sites, memory_cache(), hard=10.0)
        request = asyncio.create_task(uc.execute(make_request()))
        await asyncio.sleep(0.1)

        await uc.aclose()

        assert cancelled.is_set()
        assert [s.url for s in await request] == ["https://voe.sx/e/fast"]

    async def test_cache_off_answers_with_every_plugin(self) -> None:
        cache = memory_cache()
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "slow": fake_site([hit("https://dood.to/e/slow")], delay=0.2),
        }
        uc = cached_use_case(sites, cache, ttl=0)

        streams = await uc.execute(make_request())

        assert sorted(s.url for s in streams) == [
            "https://dood.to/e/slow",
            "https://voe.sx/e/fast",
        ]
        cache.get.assert_not_awaited()
        cache.set.assert_not_awaited()


class TestCompletion:
    """An entry whose search ended with plugins missing is completed once:
    a search for those plugins only, merged per plugin, ``missing`` cleared
    at its end whatever each plugin's outcome (continue-cut-searches)."""

    def _partial(self, cache: AsyncMock, missing: tuple[str, ...] = ("b",)) -> None:
        cache.data[SEARCH_KEY] = CachedSearch(
            results=[_of("a", "https://voe.sx/e/old")],
            total=1,
            stored_at=time.time() - 60,
            missing=missing,
        )

    async def test_the_missing_plugins_are_searched_once(self) -> None:
        cache = memory_cache()
        self._partial(cache)
        sites = {
            "a": fake_site([hit("https://voe.sx/e/new")]),
            "b": fake_site([hit("https://dood.to/e/b")], delay=0.1),
        }
        uc = cached_use_case(sites, cache)

        answers = await asyncio.gather(
            uc.execute(make_request()), uc.execute(make_request())
        )

        assert [[s.url for s in streams] for streams in answers] == [
            ["https://voe.sx/e/old"]
        ] * 2
        await eventually(lambda: cache.data[SEARCH_KEY].missing == ())
        assert cached_links(cache) == ["https://dood.to/e/b", "https://voe.sx/e/old"]
        assert sites["a"].isolated_search.await_count == 0
        assert sites["b"].isolated_search.await_count == 1
        assert cache.data[SEARCH_KEY].stored_at < time.time() - 59

    async def test_a_plugin_failing_again_leaves_a_complete_entry(self) -> None:
        cache = memory_cache()
        self._partial(cache)
        b = fake_site([])
        b.isolated_search.side_effect = RuntimeError("boom")
        uc = cached_use_case({"a": fake_site([]), "b": b}, cache)

        await uc.execute(make_request())
        await eventually(lambda: cache.data[SEARCH_KEY].missing == ())
        await uc.execute(make_request())
        await asyncio.sleep(0.05)

        assert cached_links(cache) == ["https://voe.sx/e/old"]
        assert b.isolated_search.await_count == 1

    async def test_a_completion_waits_for_the_background_slot(self) -> None:
        """A stale refresh for another title holds the one background slot."""
        cache = memory_cache()
        self._partial(cache)
        other = "stremio:search:v3:movie:tt7654321:None:None:-"
        cache.data[other] = CachedSearch(
            results=[hit("https://voe.sx/e/other")],
            total=1,
            stored_at=time.time() - SEARCH_TTL - 1,
        )
        running = peak = 0

        async def _search(*_args: object, **_kwargs: object) -> list[SearchResult]:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(0.2)
            finally:
                running -= 1
            return [hit("https://voe.sx/e/new")]

        b = fake_site([])
        b.isolated_search.side_effect = _search
        sites = {"a": fake_site([]), "b": b}
        uc = cached_use_case(sites, cache, hard=0.5)

        await uc.execute(make_request(imdb_id="tt7654321"))
        await uc.execute(make_request())

        await eventually(lambda: cache.data[SEARCH_KEY].missing == ())
        assert peak == 1


class TestRefreshMerges:
    async def test_a_cut_plugin_keeps_its_earlier_results(self) -> None:
        """The refresh ends with a finished with 1 new result and b timed
        out: the entry holds a's new result and b's earlier ones, b missing."""
        cache = memory_cache()
        cache.data[SEARCH_KEY] = CachedSearch(
            results=[
                _of("a", "https://voe.sx/e/a-old"),
                _of("b", "https://dood.to/e/b1"),
                _of("b", "https://dood.to/e/b2"),
            ],
            total=3,
            stored_at=time.time() - SEARCH_TTL - 1,
        )
        sites = {
            "a": fake_site([hit("https://voe.sx/e/a-new")]),
            "b": fake_site([hit("https://dood.to/e/b3")], delay=30),
        }
        uc = cached_use_case(sites, cache, hard=0.2)

        await uc.execute(make_request())

        await eventually(lambda: cache.data[SEARCH_KEY].missing == ("b",))
        assert cached_links(cache) == [
            "https://dood.to/e/b1",
            "https://dood.to/e/b2",
            "https://voe.sx/e/a-new",
        ]
        assert time.time() - cache.data[SEARCH_KEY].stored_at < SEARCH_TTL
        await uc.aclose()


class TestBrowserWarmup:
    """Tests for the fire-and-forget browser pre-warm in PluginSearchRunner."""

    @pytest.mark.asyncio
    async def test_warmup_fn_called_when_configured(self) -> None:
        """Browser warmup fn fires when configured, regardless of plugin mode."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Test", year=2024)
        )

        sr = make_search_result()
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
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
            config=make_config(),
            sorter=StreamSorter(make_config()),
            convert_fn=convert_search_results,
            filter_fn=filter_by_title_match,
            episode_filter_fn=filter_by_episode,
            user_agent=DEFAULT_USER_AGENT,
            max_results_var=search_max_results,
            browser_warmup_fn=warmup_fn,
            pool=ConcurrencyPool(httpx_slots=100, pw_slots=100),
        )

        await uc.execute(make_request())

        warmup_fn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warmup_fn_not_called_when_not_configured(self) -> None:
        """When no warmup fn is provided, no crash occurs."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Test", year=2024)
        )

        sr = make_search_result()
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin
        plugins.get_languages.return_value = ["de"]

        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)

        # Should not crash when browser_warmup_fn is None
        result = await uc.execute(make_request())
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_pw_semaphore_dynamic_sizing(self) -> None:
        """PW semaphore is capped at the actual PW plugin count."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Test", year=2024)
        )

        sr = make_search_result()
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
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

        config = make_config(max_concurrent_playwright=10)
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

        result = await uc.execute(make_request())
        # All 3 plugins searched — dynamic semaphore doesn't block
        assert plugins.get.call_count >= 3
        assert isinstance(result, list)
