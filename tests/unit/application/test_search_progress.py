"""Tests for SearchProgress (results of a running Stremio search)."""

from __future__ import annotations

import asyncio
import time

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
        entry = CachedSearch(results=[_sr("a")], total=3, stored_at=1.0)

        progress = SearchProgress.finished(entry)

        assert progress.done
        assert [r.download_link for r in progress.results] == ["a"]
        assert progress.total == 3

    async def test_wait_ends_with_the_search_or_the_deadline(self) -> None:
        progress = SearchProgress()
        started = time.monotonic()

        await progress.wait(time.monotonic() + 0.05)
        assert 0.04 < time.monotonic() - started < 1

        asyncio.get_running_loop().call_later(0.02, progress.finish)
        await progress.wait(time.monotonic() + 5)
        assert progress.done
