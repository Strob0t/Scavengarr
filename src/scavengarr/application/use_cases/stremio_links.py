"""The stored stream links behind ``/play`` and the HLS proxy.

Stremio keeps a stream object: autoplay plays the next episode's stream
about an hour after it was fetched, "Continue Watching" days later. The
answers therefore point every stream at Scavengarr, and the link behind it
resolves the hoster URL again when its video URL is stale or the CDN
refused it (measure 10 of ``docs/plans/round5-measures.md``).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from typing import Protocol

import structlog

from scavengarr.application.stremio.stream_builder import is_direct_video_url
from scavengarr.domain.entities.stremio import CachedStreamLink, ResolvedStream
from scavengarr.domain.ports.stream_link_repository import StreamLinkRepository

log = structlog.get_logger(__name__)

# A video URL is used this long after its resolution, as long as the
# resolver caches a stream; every working link of the measurement still
# played after 92 minutes (2026-10-05)
_FRESH_S = 3600.0


class _Resolver(Protocol):
    async def resolve(
        self, url: str, hoster: str = "", *, refresh: bool = False
    ) -> ResolvedStream | None: ...


class StremioLinks:
    """Stored stream links, resolved again when stale or refused.

    Requests for one link share one resolution: one tap on Android sent 11
    requests (a HEAD from the streaming server, GETs from the players).
    """

    def __init__(self, *, repo: StreamLinkRepository, resolver: _Resolver) -> None:
        self._repo = repo
        self._resolver = resolver
        self._running: dict[str, asyncio.Task[CachedStreamLink | None]] = {}

    async def get(self, stream_id: str) -> CachedStreamLink | None:
        """The stored link, as it is."""
        return await self._repo.get(stream_id)

    async def current(self, link: CachedStreamLink) -> CachedStreamLink | None:
        """*link* while its video URL is fresh, else resolved again.

        When the hoster gives no video, the stale video URL stays: its CDN
        can still serve it (3 FireStream links of the dev-server end-to-end
        run, 2026-10-05), and a refusal resolves again past the resolver's
        cache (``refreshed``). ``None`` without a video URL to fall back to.
        """
        if link.video_url and time.time() - link.resolved_at < _FRESH_S:
            return link
        fresh = await self._resolve_again(link, refresh=False)
        if fresh is None and link.video_url:
            return link
        return fresh

    async def refreshed(self, link: CachedStreamLink) -> CachedStreamLink | None:
        """*link* resolved again past the resolver's cache (the CDN refused it)."""
        return await self._resolve_again(link, refresh=True)

    async def aclose(self) -> None:
        """Cancel the resolutions still running (shutdown)."""
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _resolve_again(
        self, link: CachedStreamLink, *, refresh: bool
    ) -> CachedStreamLink | None:
        task = self._running.get(link.stream_id)
        if task is None:
            task = asyncio.ensure_future(self._resolve(link, refresh=refresh))
            self._running[link.stream_id] = task
            task.add_done_callback(lambda _: self._running.pop(link.stream_id, None))
        # A request that goes away (players cancel many) ends no shared work
        return await asyncio.shield(task)

    async def _resolve(
        self, link: CachedStreamLink, *, refresh: bool
    ) -> CachedStreamLink | None:
        resolved = await self._resolver.resolve(
            link.hoster_url, link.hoster, refresh=refresh
        )
        if resolved is None or not is_direct_video_url(resolved, link.hoster_url):
            log.warning(
                "stremio_link_resolve_failed",
                stream_id=link.stream_id,
                hoster=link.hoster,
                refresh=refresh,
            )
            return None
        fresh = replace(
            link,
            video_url=resolved.video_url,
            video_headers=json.dumps(resolved.headers) if resolved.headers else "",
            is_hls=resolved.is_hls,
            resolved_at=time.time(),
        )
        try:
            await self._repo.save(fresh)
        except Exception:
            # It plays now; the next request resolves again
            log.warning(
                "stremio_link_save_failed", stream_id=link.stream_id, exc_info=True
            )
        log.info(
            "stremio_link_resolved_again",
            stream_id=link.stream_id,
            hoster=link.hoster,
            refresh=refresh,
            age_s=round(time.time() - link.resolved_at) if link.resolved_at else None,
        )
        return fresh
