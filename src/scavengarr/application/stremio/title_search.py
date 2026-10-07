"""The plugin search for a Stremio request's title: cached, shared while it runs.

Requests for one title (``search_cache_key``) share one running search
(single-flight) and read its title-matching results while it runs. The
results are cached per title (``SearchCache``); a stale entry still answers
while a background search refreshes it (stale-while-revalidate, one title
at a time).
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, Literal, Protocol

import structlog

from scavengarr.application.stremio.plugin_search import PluginSearchRunner
from scavengarr.application.stremio.queries import (
    build_lang_group_queries,
    build_multi_lang_reference,
)
from scavengarr.application.stremio.search_cache import CachedSearch, SearchCache
from scavengarr.application.stremio.search_progress import SearchProgress
from scavengarr.application.stremio.title_resolution import TitleResolver
from scavengarr.domain.entities.stremio import StremioStreamRequest, TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.domain.ports.concurrency import (
    ConcurrencyBudgetPort,
    ConcurrencyPoolPort,
)
from scavengarr.domain.ports.telemetry import TelemetryPort

log = structlog.get_logger(__name__)

# Stale refreshes and completion searches run one title at a time: every
# stale title asked for started its refresh at once, and the refreshes split
# the plugin slots with the requests' own searches (fair share). Both merge
# per plugin: a plugin cut short keeps its earlier results in the entry
_BACKGROUND_SEARCHES = 1

# Where a request's search results come from: a fresh or stale cache entry,
# a new search, or a running one it joins
SearchSource = Literal["cache", "stale", "search", "joined"]

# Runs a search as a task that may outlive the request: the use case's task
# registry, which its aclose() cancels
Spawn = Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]]


class _Search(Protocol):
    """The search for one cache key; its answer budget counts from *started*."""

    def __call__(
        self, progress: SearchProgress, *, started: float
    ) -> Coroutine[Any, Any, None]: ...


class TitleSearch:
    """One shared, cached plugin search per title."""

    def __init__(
        self,
        *,
        search_runner: PluginSearchRunner,
        titles: TitleResolver,
        search_cache: SearchCache,
        pool: ConcurrencyPoolPort,
        telemetry: TelemetryPort,
        plugin_timeout_s: float,
        spawn: Spawn,
    ) -> None:
        self._search_runner = search_runner
        self._titles = titles
        self._search_cache = search_cache
        self._pool = pool
        self._telemetry = telemetry
        self._plugin_timeout_s = plugin_timeout_s
        self._spawn = spawn
        # Running searches per cache key (single-flight)
        self._searches: dict[str, SearchProgress] = {}
        self._background_searches = asyncio.Semaphore(_BACKGROUND_SEARCHES)

    async def progress(
        self,
        key: str,
        request: StremioStreamRequest,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        category: int,
        *,
        started: float,
        scored: bool,
    ) -> tuple[SearchProgress, SearchSource]:
        """The results for *key*: from the cache, or of the running or a new search.

        Requests for one key share one running search (single-flight) and
        read its results while it runs; one arriving after the search's
        answer budget does not wait for it (source ``cache``). A stale entry
        still answers while a background search refreshes it
        (stale-while-revalidate), an entry with plugins missing while one
        completes it; both one title at a time. The search runs as its own
        task, so a request that goes away does not cancel it for the
        others. Returns the progress and where it came from.
        """

        def search(groups: dict[tuple[str, ...], list[str]]) -> _Search:
            return partial(
                self._search, key, groups, title_infos, request, category, scored=scored
            )

        entry = await self._search_cache.get(key)
        if entry is None:
            return self._shared_search(
                key,
                partial(search(lang_groups), started=started),
                budget_ends=started + self._plugin_timeout_s,
            )
        stale = self._search_cache.is_stale(entry)
        if stale:
            self._shared_search(
                key, partial(self._refresh, search(lang_groups)), base=entry
            )
        elif entry.missing:
            groups = _only(lang_groups, entry.missing)
            self._shared_search(
                key,
                partial(self._complete, search(groups), entry.missing),
                base=entry,
                completes=True,
            )
        log.info(
            "stremio_search_cache_hit",
            cache_key=key,
            stale=stale,
            age_s=round(time.time() - entry.stored_at),
            result_count=len(entry.results),
        )
        return SearchProgress.finished(entry), "stale" if stale else "cache"

    def _shared_search(
        self,
        key: str,
        search: Callable[[SearchProgress], Coroutine[Any, Any, None]],
        *,
        budget_ends: float = math.inf,
        base: CachedSearch | None = None,
        completes: bool = False,
    ) -> tuple[SearchProgress, SearchSource]:
        """The running search for *key* (``joined`` within its answer budget,
        ``cache`` after it: the request answers with what it found so far);
        starts *search* when none runs (``search``). *base* and
        *completes* shape the entry (``SearchProgress``)."""
        progress = self._searches.get(key)
        if progress is not None:
            past_budget = time.monotonic() >= progress.budget_ends
            return progress, "cache" if past_budget else "joined"
        progress = SearchProgress(
            store=partial(self._search_cache.put, key), base=base, completes=completes
        )
        progress.budget_ends = budget_ends
        self._searches[key] = progress
        task = self._spawn(search(progress))
        task.add_done_callback(partial(self._search_done, key, progress))
        return progress, "search"

    def _search_done(
        self, key: str, progress: SearchProgress, _task: asyncio.Task[None]
    ) -> None:
        if self._searches.get(key) is progress:
            del self._searches[key]

    async def _refresh(self, search: _Search, progress: SearchProgress) -> None:
        """Refresh a stale entry: one title at a time (``_BACKGROUND_SEARCHES``);
        its budget and the entry's age count from the refresh's start, so a
        title that waited keeps its whole time. A plugin that does not
        finish keeps its earlier results in the entry and is missing."""
        async with self._background_searches:
            progress.started = time.time()
            await search(progress, started=time.monotonic())

    async def _complete(
        self, search: _Search, plugins: tuple[str, ...], progress: SearchProgress
    ) -> None:
        """Complete an entry: search its missing *plugins* only, one title at
        a time with the refreshes; the end clears ``missing`` whatever each
        plugin's outcome, so no entry is completed twice."""
        async with self._background_searches:
            await search(progress, started=time.monotonic())
        log.info(
            "stremio_search_completion",
            plugins=list(plugins),
            outcome="complete" if not progress.missing else "partial",
            unfinished=list(progress.missing),
            result_count=len(progress.results),
        )

    async def _search(
        self,
        key: str,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        progress: SearchProgress,
        *,
        started: float,
        scored: bool,
    ) -> None:
        """Search the plugins until they are done; store the matching results.

        Each plugin gets ``plugin_timeout_seconds`` from its own start; the
        answer budget, ``plugin_timeout_seconds`` from *started* (the
        request's start; a refresh's own, see ``_refresh``), only labels the
        plugins that return after it.
        Their title-matching results go into *progress* as they arrive: the
        answers do not wait for the search, they read it. The entry goes
        into the cache at the budget, naming the plugins still to come as
        missing, again after each of them, and at the end.
        """
        budget_ends = started + self._plugin_timeout_s
        progress.budget_ends = budget_ends
        self._spawn(progress.write_at(budget_ends))
        with self._telemetry.stage("stremio_phase", phase="search"):
            try:
                async with self._pool.request() as budget:
                    await self._search_lang_groups(
                        lang_groups,
                        title_infos,
                        request,
                        category,
                        progress,
                        scored=scored,
                        budget=budget,
                        budget_ends=budget_ends,
                    )
            finally:
                progress.finish()
        await progress.write()

    async def _search_lang_groups(
        self,
        lang_groups: dict[tuple[str, ...], list[str]],
        title_infos: dict[str, TitleMatchInfo | None],
        request: StremioStreamRequest,
        category: int,
        progress: SearchProgress,
        *,
        scored: bool,
        budget: ConcurrencyBudgetPort,
        budget_ends: float,
    ) -> None:
        """Search each language group; title-filter each plugin's results.

        Language groups are searched in parallel so that e.g. German and
        English plugins start at the same time instead of sequentially.
        Each plugin's results are filtered with its group's reference title
        when they arrive and go into *progress*.
        """

        async def _search_one_group(
            lang_key: tuple[str, ...],
            group_plugins: list[str],
        ) -> None:
            plugin_langs = list(lang_key)
            ref = build_multi_lang_reference(title_infos, plugin_langs)
            if ref is None:
                return

            queries = build_lang_group_queries(title_infos, plugin_langs)
            if not queries:
                return

            log.info(
                "stremio_search_start",
                imdb_id=request.imdb_id,
                title=ref.title,
                queries=queries,
                plugin_count=len(group_plugins),
                languages=plugin_langs,
                scored=scored,
            )

            async def _found(results: list[SearchResult]) -> None:
                progress.add(results, await self._titles.matching(results, ref))

            progress.expect(group_plugins)
            await self._search_runner.search_with_fallback(
                group_plugins,
                queries,
                category,
                season=request.season,
                episode=request.episode,
                budget=budget,
                budget_ends=budget_ends,
                on_results=_found,
                on_plugin_done=progress.plugin_done,
            )

        await asyncio.gather(
            *(
                _search_one_group(lang_key, group_plugins)
                for lang_key, group_plugins in lang_groups.items()
            )
        )


def _only(
    lang_groups: dict[tuple[str, ...], list[str]], names: tuple[str, ...]
) -> dict[tuple[str, ...], list[str]]:
    """*lang_groups* reduced to the plugins in *names* (a completion search
    asks the missing ones only); groups without one are dropped."""
    groups = {
        lang_key: [name for name in plugins if name in names]
        for lang_key, plugins in lang_groups.items()
    }
    return {lang_key: plugins for lang_key, plugins in groups.items() if plugins}
