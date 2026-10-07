"""Tests for SearchProgress (results of a running Stremio search)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from scavengarr.application.stremio.search_cache import CachedSearch
from scavengarr.application.stremio.search_progress import SearchProgress
from scavengarr.domain.plugins.base import SearchResult


def _sr(link: str, plugin: str = "a") -> SearchResult:
    return SearchResult(
        title="Iron Man", download_link=link, metadata={"source_plugin": plugin}
    )


class TestSearchProgress:
    def test_keeps_matching_results_once(self) -> None:
        """The full and the base title find many links twice."""
        progress = SearchProgress()

        progress.add([_sr("a"), _sr("b"), _sr("x")], [_sr("a"), _sr("b")])
        progress.add([_sr("a"), _sr("c")], [_sr("a"), _sr("c")])

        assert [r.download_link for r in progress.results] == ["a", "b", "c"]
        assert progress.total == 4  # a, b, x, c before the title filter

    def test_other_results_with_the_same_link_stay(self) -> None:
        """Another plugin's or another release's list of links can hold other
        hosters."""
        progress = SearchProgress()
        other_release = SearchResult(
            title="Iron Man",
            download_link="a",
            release_name="Iron.Man.720p",
            metadata={"source_plugin": "a"},
        )

        progress.add([_sr("a")], [_sr("a")])
        progress.add([_sr("a", "b")], [_sr("a", "b")])
        progress.add([other_release], [other_release])

        assert len(progress.results) == 3
        assert progress.total == 3

    def test_results_with_other_hoster_links_stay(self) -> None:
        """Plugins store a link's URL under ``link``; the key read ``url``,
        so such results collapsed into one (code review, 2026-10-06)."""
        progress = SearchProgress()
        results = [
            SearchResult(
                title="Iron Man",
                download_link="https://site.example/film",
                download_links=[{"hoster": "voe", "link": url}],
                metadata={"source_plugin": "a"},
            )
            for url in ("https://voe.sx/e/1", "https://voe.sx/e/2")
        ]

        progress.add(results, results)

        assert len(progress.results) == 2
        assert progress.total == 2

    def test_a_link_another_group_matches_is_kept(self) -> None:
        """Language groups filter with their own title."""
        progress = SearchProgress()

        progress.add([_sr("a")], [])
        progress.add([_sr("a")], [_sr("a")])

        assert [r.download_link for r in progress.results] == ["a"]
        assert progress.total == 1

    def test_listeners_hear_of_results_and_the_end(self) -> None:
        progress = SearchProgress()
        event = asyncio.Event()
        progress.listen(event)

        progress.add([_sr("a")], [_sr("a")])
        assert event.is_set()
        event.clear()
        progress.finish()
        assert event.is_set() and progress.done

        event.clear()
        progress.unlisten(event)
        progress.add([_sr("b")], [_sr("b")])
        assert not event.is_set()

    def test_entry_of_the_results(self) -> None:
        progress = SearchProgress()
        progress.add([_sr("a"), _sr("x")], [_sr("a")])

        entry = progress.entry()

        assert [r.download_link for r in entry.results] == ["a"]
        assert entry.total == 2
        assert time.time() - entry.stored_at < 5

    def test_finished_from_a_cache_entry(self) -> None:
        entry = CachedSearch(
            results=[_sr("a")], total=3, stored_at=1.0, missing=("sto",)
        )

        progress = SearchProgress.finished(entry)

        assert progress.done
        assert [r.download_link for r in progress.results] == ["a"]
        assert progress.total == 3
        assert progress.missing == ("sto",)
        assert progress.entry().stored_at == 1.0

    async def test_wait_ends_with_the_search_or_the_deadline(self) -> None:
        progress = SearchProgress()
        started = time.monotonic()

        await progress.wait(time.monotonic() + 0.05)
        assert 0.04 < time.monotonic() - started < 1

        asyncio.get_running_loop().call_later(0.02, progress.finish)
        await progress.wait(time.monotonic() + 5)
        assert progress.done


class TestMissingPlugins:
    """The entry names the plugins the search asked that have not finished
    (continue-cut-searches)."""

    async def test_expected_plugins_are_missing_until_they_finish(self) -> None:
        progress = SearchProgress()
        progress.expect(["sto", "kinoger"])

        assert progress.missing == ("kinoger", "sto")
        await progress.plugin_done("sto", True)
        assert progress.missing == ("kinoger",)
        assert progress.entry().missing == ("kinoger",)

    async def test_a_failed_plugin_stays_missing(self) -> None:
        progress = SearchProgress()
        progress.expect(["kinoger"])

        await progress.plugin_done("kinoger", False)

        assert progress.missing == ("kinoger",)

    async def test_the_budget_write_and_the_late_plugins_rewrite(self) -> None:
        store = AsyncMock()
        progress = SearchProgress(store=store)
        progress.expect(["a", "sto"])
        progress.add([_sr("a")], [_sr("a")])
        await progress.plugin_done("a", True)

        await progress.write_at(time.monotonic() + 0.02)

        first = store.await_args_list[0].args[0]
        assert [r.download_link for r in first.results] == ["a"]
        assert first.missing == ("sto",)

        progress.add([_sr("s", "sto")], [_sr("s", "sto")])
        await progress.plugin_done("sto", True)

        second = store.await_args_list[1].args[0]
        assert [r.download_link for r in second.results] == ["a", "s"]
        assert second.missing == ()
        assert second.stored_at == first.stored_at == progress.started

    async def test_a_plugin_finishing_before_the_budget_writes_nothing(
        self,
    ) -> None:
        store = AsyncMock()
        progress = SearchProgress(store=store)
        progress.expect(["a"])

        await progress.plugin_done("a", True)

        store.assert_not_awaited()

    async def test_the_budget_write_is_skipped_when_the_search_ends_first(
        self,
    ) -> None:
        store = AsyncMock()
        progress = SearchProgress(store=store)
        asyncio.get_running_loop().call_later(0.01, progress.finish)

        await progress.write_at(time.monotonic() + 5)

        store.assert_not_awaited()

    def test_the_entry_age_counts_from_the_search_start(self) -> None:
        progress = SearchProgress()
        progress.started = 1.0

        assert progress.entry().stored_at == 1.0


def _base() -> CachedSearch:
    return CachedSearch(
        results=[_sr("h1", "hdfilme"), _sr("s1", "sto"), _sr("s2", "sto")],
        total=3,
        stored_at=1.0,
        missing=("kinoger",),
    )


class TestMergingIntoABase:
    """A refresh or completion search merges into the entry it started from:
    a finished plugin's results replace its earlier ones, an unfinished
    plugin keeps them and is missing (continue-cut-searches)."""

    async def test_a_finished_plugin_replaces_its_results(self) -> None:
        progress = SearchProgress(base=_base())
        progress.expect(["hdfilme", "sto"])
        progress.add([_sr("h2", "hdfilme")], [_sr("h2", "hdfilme")])
        await progress.plugin_done("hdfilme", True)

        entry = progress.entry()

        assert [r.download_link for r in entry.results] == ["s1", "s2", "h2"]
        assert entry.missing == ("kinoger", "sto")
        assert entry.total == 3

    async def test_an_unfinished_plugin_keeps_its_results(self) -> None:
        progress = SearchProgress(base=_base())
        progress.expect(["sto"])
        progress.add([_sr("s3", "sto")], [_sr("s3", "sto")])
        await progress.plugin_done("sto", False)
        progress.finish()

        entry = progress.entry()

        assert [r.download_link for r in entry.results] == ["h1", "s1", "s2"]
        assert entry.missing == ("kinoger", "sto")

    def test_a_refresh_ages_from_its_own_start(self) -> None:
        progress = SearchProgress(base=_base())

        assert progress.entry().stored_at == progress.started != 1.0

    async def test_a_completion_keeps_the_entry_age_and_clears_missing(
        self,
    ) -> None:
        """Its end clears ``missing`` whatever each plugin's outcome, so no
        entry is completed twice; the entry keeps its results."""
        store = AsyncMock()
        progress = SearchProgress(store=store, base=_base(), completes=True)
        progress.expect(["kinoger"])
        await progress.plugin_done("kinoger", False)

        assert progress.entry().missing == ("kinoger",)
        progress.finish()
        await progress.write()

        entry = store.await_args.args[0]
        assert entry.missing == ()
        assert entry.stored_at == 1.0
        assert [r.download_link for r in entry.results] == ["h1", "s1", "s2"]
