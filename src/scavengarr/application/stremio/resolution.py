"""Hoster resolution of one Stremio request, started as results arrive.

The plugins deliver results one by one; resolving each hoster's best link
as soon as it is known puts the first streams near the search's first
results instead of after the whole search (measure 2 of
``docs/plans/round5-measures.md``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from scavengarr.application.stremio.stream_builder import (
    hoster_key,
    is_direct_video_url,
)
from scavengarr.domain.entities.stremio import RankedStream, ResolvedStream

log = structlog.get_logger(__name__)

# Resolves a hoster embed URL (url, hoster hint) to a playable video URL
ResolveCallback = Callable[[str, str], Awaitable[ResolvedStream | None]]


class HosterResolution:
    """The resolutions of one request's ranked streams.

    Each hoster (and language, ``hoster_key``) resolves its best-ranked link
    that is not known dead; its next link only after that failed, none while
    one runs or after one resolved. Resolving every candidate at once opened
    dozens of connections to distinct CDNs within a second, which the home
    router blocked like a port scan. A better-ranked link that arrives later
    resolves too. Each URL resolves once, at most *concurrency* at a time,
    among the top *limit* streams. *changed* is set whenever a resolution
    ends.
    """

    def __init__(
        self,
        resolve_fn: ResolveCallback,
        *,
        concurrency: int,
        limit: int,
        changed: asyncio.Event,
    ) -> None:
        self._resolve_fn = resolve_fn
        self._semaphore = asyncio.Semaphore(concurrency)
        self._limit = limit
        self._changed = changed
        self._ranked: list[RankedStream] = []
        self._hosters: list[list[int]] = []  # indices into _ranked, rank order
        self._tasks: dict[str, asyncio.Task[ResolvedStream | None]] = {}
        self._closed = False

    @property
    def ranked(self) -> list[RankedStream]:
        """The streams considered: the top of the latest ranking."""
        return self._ranked

    @property
    def started(self) -> int:
        return len(self._tasks)

    @property
    def unfinished(self) -> int:
        return sum(not task.done() for task in self._tasks.values())

    def update(self, ranked: list[RankedStream]) -> None:
        """Take a new ranking (more results) and start what is due.

        New results only push streams down: a resolution whose stream left
        the top *limit* can no longer be part of the answer and is
        cancelled, so the answer does not wait for it and its slot frees.
        """
        self._ranked = ranked[: self._limit]
        considered = {stream.url for stream in self._ranked}
        for url, task in self._tasks.items():
            if url not in considered:
                task.cancel()
        groups: dict[object, list[int]] = {}
        for i, stream in enumerate(self._ranked):
            groups.setdefault(hoster_key(stream) or stream.url, []).append(i)
        self._hosters = list(groups.values())
        self._start_due()

    def pending(self) -> bool:
        """Whether a resolution runs or is due (the due ones start)."""
        self._start_due()
        return self.unfinished > 0

    def resolved(self) -> dict[int, ResolvedStream]:
        """Each hoster's best resolution (a video before an echoed embed URL),
        by index into :attr:`ranked`."""
        chosen: dict[int, ResolvedStream] = {}
        for indices in self._hosters:
            results = [(i, r) for i in indices if (r := self._result(i)) is not None]
            videos = [(i, r) for i, r in results if self._is_video(i, r)]
            if best := videos or results:
                i, resolved = best[0]
                chosen[i] = resolved
        return chosen

    def videos(self) -> int:
        """How many hosters have a video."""
        return sum(self._is_video(i, r) for i, r in self.resolved().items())

    async def aclose(self) -> None:
        """Cancel the resolutions that still run; start no new ones."""
        self._closed = True
        running = [task for task in self._tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    def _start_due(self) -> None:
        """Start each hoster's best link that is neither known dead nor tried.

        Repeats until nothing more starts: with eager tasks (production) a
        cached outcome ends inside ``create_task``, and a dead one makes the
        hoster's next link due at once.
        """
        started = True
        while started and not self._closed:
            started = False
            for indices in self._hosters:
                for i in indices:
                    url = self._ranked[i].url
                    task = self._tasks.get(url)
                    if task is None:
                        self._tasks[url] = self._start(self._ranked[i])
                        started = True
                        break
                    if not task.done() or self._result(i) is not None:
                        break

    def _start(self, stream: RankedStream) -> asyncio.Task[ResolvedStream | None]:
        task = asyncio.create_task(self._resolve(stream))
        task.add_done_callback(self._ended)
        return task

    async def _resolve(self, stream: RankedStream) -> ResolvedStream | None:
        async with self._semaphore:
            try:
                return await self._resolve_fn(stream.url, stream.hoster)
            except Exception:
                log.debug(
                    "stremio_resolve_failed",
                    hoster=stream.hoster,
                    url=stream.url[:80],
                    exc_info=True,
                )
                return None

    def _ended(self, _task: asyncio.Task[ResolvedStream | None]) -> None:
        if self._closed:
            return
        self._start_due()
        self._changed.set()

    def _result(self, index: int) -> ResolvedStream | None:
        task = self._tasks.get(self._ranked[index].url)
        if task is None or not task.done() or task.cancelled():
            return None
        return task.result()

    def _is_video(self, index: int, resolved: ResolvedStream) -> bool:
        return is_direct_video_url(resolved, self._ranked[index].url)
