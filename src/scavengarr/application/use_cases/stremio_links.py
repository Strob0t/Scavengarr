"""The stored stream links behind ``/play`` and the HLS proxy.

Stremio keeps a stream object: autoplay plays the next episode's stream
about an hour after it was fetched, "Continue Watching" days later. The
answers therefore point every stream at Scavengarr, and the link behind it
resolves the hoster URL again when its video URL is stale or the CDN
refused it (measure 10 of ``docs/plans/round5-measures.md``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import replace
from typing import Any, Protocol

import structlog

from scavengarr.application.stremio.stream_builder import (
    is_direct_video_url,
    with_resolution,
)
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

    def bound_headers(self, url: str, hoster: str = "") -> tuple[str, ...]: ...

    async def resolve_for_client(
        self, url: str, hoster: str, headers: Mapping[str, str]
    ) -> ResolvedStream | None: ...


def _bound(headers: Mapping[str, str], names: tuple[str, ...]) -> dict[str, str]:
    """The non-empty values of *headers* named in *names*, by lower-case name."""
    lowered = {name.lower(): value for name, value in headers.items()}
    return {name: lowered[name] for name in names if lowered.get(name)}


def _player_link_id(stream_id: str, player: dict[str, str]) -> str:
    """The stored link of *stream_id* resolved for one player's headers."""
    digest = hashlib.sha256(json.dumps(sorted(player.items())).encode()).hexdigest()
    return f"{stream_id}-{digest[:16]}"


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

    async def current(
        self, link: CachedStreamLink, player: Mapping[str, str] | None = None
    ) -> CachedStreamLink | None:
        """*link* while its video URL is fresh, else resolved again.

        When the hoster gives no video, the stale video URL stays: its CDN
        can still serve it (3 FireStream links of the dev-server end-to-end
        run, 2026-10-05), and a refusal resolves again past the resolver's
        cache (``refreshed``). ``None`` without a video URL to fall back to.

        *player* holds the request headers of the player asking. A hoster
        whose CDN binds video URLs to some of them (VEEV) gets a link of its
        own for a player that sends other values than *link* was resolved
        with; the stored link stays the fallback.
        """
        if player is not None:
            own = await self._for_player(link, player)
            if own is not None:
                return own
        if link.video_url and time.time() - link.resolved_at < _FRESH_S:
            return link
        fresh = await self._resolve_again(link, refresh=False)
        if fresh is None and link.video_url:
            return link
        return fresh

    async def pinned(self, link: CachedStreamLink) -> CachedStreamLink:
        """*link* under an id of its own resolution, for what a playlist
        served from it lists (variants, segments).

        All answers and devices share one link per hoster URL: a later
        resolution under that id moved a running playback's segments to
        another CDN node with the old token (403, then 502; code review,
        2026-10-06). The copy stays as it is. When it cannot be saved,
        *link* itself.
        """
        base = link.stream_id.split(".", 1)[0]
        digest = hashlib.sha256(link.video_url.encode()).hexdigest()[:12]
        pinned_id = f"{base}.{digest}"
        if link.stream_id == pinned_id:
            return link
        copy = replace(link, stream_id=pinned_id)
        try:
            await self._repo.save(copy)
        except Exception:
            log.warning(
                "stremio_link_pin_failed", stream_id=link.stream_id, exc_info=True
            )
            return link
        return copy

    async def refreshed(self, link: CachedStreamLink) -> CachedStreamLink | None:
        """*link* resolved again past the resolver's cache (the CDN refused it)."""
        return await self._resolve_again(link, refresh=True)

    async def aclose(self) -> None:
        """Cancel the resolutions still running (shutdown)."""
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _for_player(
        self, link: CachedStreamLink, player: Mapping[str, str]
    ) -> CachedStreamLink | None:
        """*link* resolved for *player*, when its hoster's CDN binds headers
        the player sends other values of; ``None`` otherwise or on failure."""
        names = self._resolver.bound_headers(link.hoster_url, link.hoster)
        if not names:
            return None
        wanted = _bound(player, names)
        if wanted == _bound(json.loads(link.video_headers or "{}"), names):
            return None
        own_id = _player_link_id(link.stream_id, wanted)
        own = await self._repo.get(own_id)
        if (
            own is not None
            and own.video_url
            and time.time() - own.resolved_at < _FRESH_S
        ):
            return own
        return await self._shared(
            own_id, lambda: self._resolve_for_player(link, own_id, wanted)
        )

    async def _resolve_for_player(
        self, link: CachedStreamLink, own_id: str, player: dict[str, str]
    ) -> CachedStreamLink | None:
        resolved = await self._resolver.resolve_for_client(
            link.hoster_url, link.hoster, player
        )
        if resolved is None or not is_direct_video_url(resolved, link.hoster_url):
            log.warning(
                "stremio_link_player_resolve_failed",
                stream_id=link.stream_id,
                hoster=link.hoster,
            )
            return None
        own = await self._store(replace(link, stream_id=own_id), resolved)
        log.info(
            "stremio_link_resolved_for_player",
            stream_id=link.stream_id,
            hoster=link.hoster,
        )
        return own

    async def _resolve_again(
        self, link: CachedStreamLink, *, refresh: bool
    ) -> CachedStreamLink | None:
        """One resolution per link; a refresh joins no resolution that can
        answer from the resolver's cache (with the URL the CDN refused, code
        review 2026-10-06), but every request joins a running refresh."""
        refreshing = f"{link.stream_id}#refresh"
        if refresh or refreshing in self._running:
            return await self._shared(
                refreshing, lambda: self._resolve(link, refresh=True)
            )
        return await self._shared(
            link.stream_id, lambda: self._resolve(link, refresh=False)
        )

    async def _shared(
        self,
        key: str,
        start: Callable[[], Coroutine[Any, Any, CachedStreamLink | None]],
    ) -> CachedStreamLink | None:
        """One resolution per *key* for all requests that ask meanwhile."""
        task = self._running.get(key)
        if task is None:
            task = asyncio.ensure_future(start())
            self._running[key] = task
            task.add_done_callback(lambda _: self._running.pop(key, None))
        # A request that goes away (players cancel many) ends no shared work
        return await asyncio.shield(task)

    async def _store(
        self, link: CachedStreamLink, resolved: ResolvedStream
    ) -> CachedStreamLink:
        """*link* with the video of *resolved*, saved for the next request."""
        fresh = with_resolution(link, resolved)
        try:
            await self._repo.save(fresh)
        except Exception:
            # It plays now; the next request resolves again
            log.warning(
                "stremio_link_save_failed", stream_id=link.stream_id, exc_info=True
            )
        return fresh

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
        fresh = await self._store(link, resolved)
        log.info(
            "stremio_link_resolved_again",
            stream_id=link.stream_id,
            hoster=link.hoster,
            refresh=refresh,
            age_s=round(time.time() - link.resolved_at) if link.resolved_at else None,
        )
        return fresh
