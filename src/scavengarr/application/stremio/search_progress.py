"""Results of a running Stremio search, as the plugins deliver them.

The answer of a request no longer waits for the search: it goes out once
enough streams resolve (``StremioStreamUseCase``), so the requests on a
search read its results while it runs. The progress also knows which
plugins the search asks and which of them finished: the entry it writes at
the answer budget names the rest as missing, and every plugin that
finishes later rewrites it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Iterable

from scavengarr.application.stremio.search_cache import CachedSearch
from scavengarr.domain.plugins.base import ResultKey, SearchResult, result_key

# Stores the search's entry (the search cache, under the search's key)
StoreFn = Callable[[CachedSearch], Awaitable[None]]


class SearchProgress:
    """The title-matching results of one search so far, and whether it is done.

    Shared by every request waiting on the search (single-flight). A result
    is kept once: the full and the base title find many results of a plugin
    twice. Listeners (events) are set on every change. The entry's
    ``stored_at`` is the progress's creation, the search's start, so its
    age does not move with late writes.
    """

    def __init__(self, *, store: StoreFn | None = None) -> None:
        self.results: list[SearchResult] = []
        self.total = 0  # results before the title filter
        self.done = False
        self.started = time.time()
        self._store = store
        self._expected: set[str] = set()  # the plugins the search asks
        self._finished: set[str] = set()  # of them, the ones that finished
        self._written = False  # the entry was written at the budget
        self._changed = True  # since the last write
        self._writing = asyncio.Lock()  # writes land in order
        self._matching: set[ResultKey] = set()
        self._found: set[ResultKey] = set()
        self._listeners: set[asyncio.Event] = set()
        self._ended = asyncio.Event()

    @classmethod
    def finished(cls, entry: CachedSearch) -> SearchProgress:
        """The progress of a search that is over (a cache entry)."""
        progress = cls()
        progress.results = list(entry.results)
        progress.total = entry.total
        progress.started = entry.stored_at
        progress._expected = set(entry.missing)
        progress.finish()
        return progress

    @property
    def missing(self) -> tuple[str, ...]:
        """The plugins asked that have not finished; a plugin that timed
        out, failed or was cancelled stays among them."""
        return tuple(sorted(self._expected - self._finished))

    def expect(self, names: Iterable[str]) -> None:
        """The plugins the search asks: each is missing until it finishes."""
        self._expected.update(names)

    async def plugin_done(self, name: str, finished: bool) -> None:
        """A plugin's search ended, *finished* when its results are whole
        (not cut by a timeout, an error or a cancellation).

        A plugin that finishes after the budget's write rewrites the entry:
        its results replace its earlier ones, its name leaves ``missing``.
        """
        if not finished:
            return
        self._finished.add(name)
        self._changed = True
        if self._written:
            await self.write()

    def add(self, found: list[SearchResult], matching: list[SearchResult]) -> None:
        """A plugin's results (*found*) and those of them matching the title."""
        keys = {result_key(r) for r in found} - self._found
        self._found |= keys
        self.total += len(keys)
        for result in matching:
            if (key := result_key(result)) not in self._matching:
                self._matching.add(key)
                self.results.append(result)
        self._changed = True
        self._notify()

    def finish(self) -> None:
        self.done = True
        self._ended.set()
        self._notify()

    def entry(self) -> CachedSearch:
        """The results as a cache entry."""
        return CachedSearch(
            results=list(self.results),
            total=self.total,
            stored_at=self.started,
            missing=self.missing,
        )

    async def write(self) -> None:
        """Store the entry through the search's store when it changed since
        the last write (without a store: nothing)."""
        self._written = True
        async with self._writing:
            if self._changed and self._store is not None:
                self._changed = False
                await self._store(self.entry())

    async def write_at(self, when: float) -> None:
        """Write the entry at *when* (monotonic), the answer budget, unless
        the search ends first: its end writes the entry."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(max(when - time.monotonic(), 0.0)):
                await self._ended.wait()
                return
        if not self.done:
            await self.write()

    async def wait(self, deadline: float) -> None:
        """Until the search is done, at the latest at *deadline* (monotonic)."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(max(deadline - time.monotonic(), 0.0)):
                await self._ended.wait()

    def listen(self, event: asyncio.Event) -> None:
        self._listeners.add(event)

    def unlisten(self, event: asyncio.Event) -> None:
        self._listeners.discard(event)

    def _notify(self) -> None:
        for event in self._listeners:
            event.set()
