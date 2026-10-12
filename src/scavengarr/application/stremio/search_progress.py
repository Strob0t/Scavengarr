"""Results of a running Stremio search, as the plugins deliver them.

The answer of a request no longer waits for the search: it goes out once
enough streams resolve (``StremioStreamUseCase``), so the requests on a
search read its results while it runs. The progress also knows which
plugins the search asks and which of them finished: the entry it writes at
the answer budget names the rest as missing, and every plugin that
finishes later rewrites it. A refresh or completion search merges into the
entry it started from (``base``), plugin by plugin.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace

import structlog

from scavengarr.application.stremio.search_cache import CachedSearch, merge
from scavengarr.domain.plugins.base import ResultKey, SearchResult, result_key

log = structlog.get_logger(__name__)

# Stores the search's entry (the search cache, under the search's key)
StoreFn = Callable[[CachedSearch], Awaitable[None]]


class SearchProgress:
    """The title-matching results of one search so far, and whether it is done.

    Shared by every request waiting on the search (single-flight). A result
    is kept once: the full and the base title find many results of a plugin
    twice. Listeners (events) are set on every change. The entry's
    ``stored_at`` is the progress's creation, the search's start, so its
    age does not move with late writes.

    *base* is the entry a refresh or completion search merges into: the
    entry written replaces the results of the plugins that finished and
    keeps the others', so no merge thins an entry. With *completes* the
    search completes the base entry: it keeps the entry's age, and its end
    clears ``missing`` whatever each plugin's outcome (one completion per
    entry).

    A partial match (``TitleMatches.partial``: a series named only by a
    shorter form of the request's titles) waits for its plugin's end. It
    counts only where the plugin gave no other match: the full and the base
    title run at once, and "Naruto" beside "Naruto Shippuden" is the parent
    series of "Naruto: Shippūden", while "Naruto" alone may be the site's
    name for it.
    """

    def __init__(
        self,
        *,
        store: StoreFn | None = None,
        base: CachedSearch | None = None,
        completes: bool = False,
    ) -> None:
        self.results: list[SearchResult] = []
        self.total = 0  # results before the title filter
        self.done = False
        self.started = base.stored_at if completes and base else time.time()
        # The search's answer budget (``time.monotonic()``) once it runs
        # (``TitleSearch``): a request arriving after it does not wait for
        # the search; a finished search has none
        self.budget_ends = math.inf
        self._store = store
        self._base = base
        self._completes = completes
        self.dropped = 0  # the results the episode filter dropped
        self._expected: set[str] = set()  # the plugins the search asks
        self._finished: set[str] = set()  # of them, the ones that finished
        self._written = False  # the entry was written at the budget
        self._changed = True  # since the last write
        self._writing = asyncio.Lock()  # writes land in order
        self._matching: set[ResultKey] = set()
        self._found: set[ResultKey] = set()
        # Per plugin: its partial matches waiting for its end, and whether
        # it gave another match
        self._held: dict[str, list[SearchResult]] = {}
        self._matched: set[str] = set()
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

    async def plugin_done(self, name: str, finished: bool, dropped: int = 0) -> None:
        """A plugin's search ended, *finished* when its results are whole
        (not cut by a timeout, an error or a cancellation), with the number
        of results the episode filter *dropped* in its runs.

        A plugin that finishes after the budget's write rewrites the entry:
        its results replace its earlier ones, its name leaves ``missing``.
        """
        self.dropped += dropped
        self._release(name)
        if not finished:
            return
        self._finished.add(name)
        self._changed = True
        if self._written:
            await self.write()

    def add(
        self,
        found: list[SearchResult],
        matching: list[SearchResult],
        partial: Iterable[SearchResult] = (),
    ) -> None:
        """A plugin's results (*found*), those of them matching the title, and
        its *partial* matches, which wait for the plugin's end."""
        keys = {result_key(r) for r in found} - self._found
        self._found |= keys
        self.total += len(keys)
        dropped: Counter[str] = Counter()
        for result in matching:
            plugin = _plugin(result)
            self._matched.add(plugin)
            dropped[plugin] += len(self._held.pop(plugin, ()))
            self._keep(result)
        for result in partial:
            plugin = _plugin(result)
            if plugin in self._matched:
                dropped[plugin] += 1
            else:
                self._held.setdefault(plugin, []).append(result)
        for plugin, count in dropped.items():
            if count:
                log.info(
                    "stremio_partial_matches_dropped",
                    plugin=plugin,
                    results_count=count,
                )
        self._changed = True
        self._notify()

    def _keep(self, result: SearchResult) -> None:
        if (key := result_key(result)) not in self._matching:
            self._matching.add(key)
            self.results.append(result)

    def _release(self, plugin: str) -> None:
        """*plugin* ended without another match: its partial matches count."""
        held = self._held.pop(plugin, [])
        for result in held:
            self._keep(result)
        if held:
            self._changed = True
            self._notify()

    def finish(self) -> None:
        # A standby's results carry its own name, its end is reported under
        # its mirror group's member: what still waits counts now
        for plugin in list(self._held):
            self._release(plugin)
        self.done = True
        if self._completes:
            self._changed = True  # the end clears ``missing``
        self._ended.set()
        self._notify()

    def entry(self) -> CachedSearch:
        """The results as a cache entry: with a base, the base with each
        finished plugin's results replaced and the unfinished plugins of
        both missing."""
        if self._base is None:
            return CachedSearch(
                results=list(self.results),
                total=self.total,
                stored_at=self.started,
                missing=self.missing,
            )
        entry = self._base
        for name in sorted(self._finished):
            entry = merge(entry, name, self._results_of(name))
        missing: tuple[str, ...] = ()
        if not (self._completes and self.done):
            missing = tuple(sorted({*entry.missing, *self.missing}))
        return replace(entry, stored_at=self.started, missing=missing)

    def _results_of(self, plugin: str) -> list[SearchResult]:
        return [r for r in self.results if _plugin(r) == plugin]

    async def write(self) -> None:
        """Store the entry through the search's store when it changed since
        the last write (without a store: nothing)."""
        self._written = True
        async with self._writing:
            if self._changed and self._store is not None:
                self._changed = False
                await self._store(self.entry())

    async def write_at(self, when: float) -> bool:
        """Write the entry at *when* (monotonic), the answer budget, unless
        the search ends first: its end writes the entry. Returns whether
        the search was still running at *when*."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(max(when - time.monotonic(), 0.0)):
                await self._ended.wait()
                return False
        if self.done:
            return False
        await self.write()
        return True

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


def _plugin(result: SearchResult) -> str:
    """The plugin that gave *result* (``source_plugin``, set by the runner)."""
    return str(result.metadata.get("source_plugin", ""))
