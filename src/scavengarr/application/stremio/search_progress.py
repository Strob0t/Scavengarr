"""Results of a running Stremio search, as the plugins deliver them.

The answer of a request no longer waits for the search: it goes out once
enough streams resolve (``StremioStreamUseCase``), so the requests on a
search read its results while it runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from scavengarr.application.stremio.search_cache import CachedSearch
from scavengarr.domain.plugins.base import ResultKey, SearchResult, result_key


class SearchProgress:
    """The title-matching results of one search so far, and whether it is done.

    Shared by every request waiting on the search (single-flight). A result
    is kept once: the full and the base title find many results of a plugin
    twice. Listeners (events) are set on every change.
    """

    def __init__(self) -> None:
        self.results: list[SearchResult] = []
        self.total = 0  # results before the title filter
        self.done = False
        self._matching: set[ResultKey] = set()
        self._found: set[ResultKey] = set()
        self._listeners: set[asyncio.Event] = set()
        self._finished = asyncio.Event()

    @classmethod
    def finished(cls, entry: CachedSearch) -> SearchProgress:
        """The progress of a search that is over (a cache entry)."""
        progress = cls()
        progress.results = list(entry.results)
        progress.total = entry.total
        progress.finish()
        return progress

    def add(self, found: list[SearchResult], matching: list[SearchResult]) -> None:
        """A plugin's results (*found*) and those of them matching the title."""
        keys = {result_key(r) for r in found} - self._found
        self._found |= keys
        self.total += len(keys)
        for result in matching:
            if (key := result_key(result)) not in self._matching:
                self._matching.add(key)
                self.results.append(result)
        self._notify()

    def finish(self) -> None:
        self.done = True
        self._finished.set()
        self._notify()

    def entry(self) -> CachedSearch:
        """The results as a cache entry."""
        return CachedSearch(
            results=list(self.results), total=self.total, stored_at=time.time()
        )

    async def wait(self, deadline: float) -> None:
        """Until the search is done, at the latest at *deadline* (monotonic)."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(max(deadline - time.monotonic(), 0.0)):
                await self._finished.wait()

    def listen(self, event: asyncio.Event) -> None:
        self._listeners.add(event)

    def unlisten(self, event: asyncio.Event) -> None:
        self._listeners.discard(event)

    def _notify(self) -> None:
        for event in self._listeners:
            event.set()
