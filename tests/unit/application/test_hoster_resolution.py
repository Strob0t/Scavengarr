"""Tests for HosterResolution (one request's resolutions, as results arrive)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

import structlog

from scavengarr.application.stremio.resolution import HosterResolution
from scavengarr.domain.entities.stremio import RankedStream, ResolvedStream
from scavengarr.domain.ports.browser_fetcher import PageClaim, page_claim

_VOE = RankedStream(url="https://voe.sx/e/best", hoster="voe")
_VOE_2 = RankedStream(url="https://voe.sx/e/second", hoster="voe")
_DOOD = RankedStream(url="https://dood.to/e/one", hoster="doodstream")
_FILEMOON = RankedStream(url="https://filemoon.sx/e/one", hoster="filemoon")


def _video(url: str) -> ResolvedStream:
    return ResolvedStream(
        video_url=f"https://cdn.example/{url.rsplit('/', 1)[-1]}.mp4",
        headers={"Referer": url},
    )


class _Resolver:
    """Resolves after *delay*; *dead* links fail, *echo* links echo the URL."""

    def __init__(
        self,
        *,
        dead: tuple[str, ...] = (),
        echo: tuple[str, ...] = (),
        delay: float = 0.0,
    ) -> None:
        self.dead, self.echo, self.delay = dead, echo, delay
        self.calls: list[str] = []
        self.running = 0
        self.most = 0

    async def __call__(self, url: str, hoster: str) -> ResolvedStream | None:
        self.calls.append(url)
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if url in self.dead:
                return None
            if url in self.echo:
                return ResolvedStream(video_url=url)
            return _video(url)
        finally:
            self.running -= 1


def _resolution(
    resolve: Callable[[str, str], Awaitable[ResolvedStream | None]],
    *,
    concurrency: int = 10,
    limit: int = 50,
) -> tuple[HosterResolution, asyncio.Event]:
    changed = asyncio.Event()
    return (
        HosterResolution(
            resolve, concurrency=concurrency, limit=limit, changed=changed
        ),
        changed,
    )


async def _settle(resolution: HosterResolution) -> None:
    while resolution.pending():
        await asyncio.sleep(0.005)


def _urls(resolution: HosterResolution) -> dict[str, str]:
    return {
        resolution.ranked[i].url: r.video_url for i, r in resolution.resolved().items()
    }


class TestRankOrder:
    async def test_each_hoster_resolves_its_best_link_first(self) -> None:
        """Bursts to dozens of CDNs look like a port scan to home routers:
        a hoster's next link is tried only after its better one failed."""
        resolver = _Resolver()
        resolution, _ = _resolution(resolver)

        resolution.update([_VOE, _VOE_2, _DOOD])
        await _settle(resolution)

        assert sorted(resolver.calls) == [_DOOD.url, _VOE.url]
        assert _urls(resolution) == {
            _VOE.url: "https://cdn.example/best.mp4",
            _DOOD.url: "https://cdn.example/one.mp4",
        }

    async def test_the_next_link_after_a_failure(self) -> None:
        resolver = _Resolver(dead=(_VOE.url,))
        resolution, _ = _resolution(resolver)

        resolution.update([_VOE, _VOE_2])
        await _settle(resolution)

        assert resolver.calls == [_VOE.url, _VOE_2.url]
        assert list(_urls(resolution)) == [_VOE_2.url]

    async def test_a_failing_resolver_counts_as_dead(self) -> None:
        async def _resolve(url: str, hoster: str) -> ResolvedStream | None:
            if url == _VOE.url:
                raise RuntimeError("boom")
            return _video(url)

        resolution, _ = _resolution(_resolve)

        resolution.update([_VOE, _VOE_2])
        await _settle(resolution)

        assert list(_urls(resolution)) == [_VOE_2.url]

    async def test_a_better_link_that_arrives_later_resolves_too(self) -> None:
        resolver = _Resolver()
        resolution, _ = _resolution(resolver)
        resolution.update([_VOE_2])
        await _settle(resolution)

        resolution.update([_VOE, _VOE_2])
        await _settle(resolution)

        assert resolver.calls == [_VOE_2.url, _VOE.url]
        assert list(_urls(resolution)) == [_VOE.url]

    async def test_a_video_wins_over_a_better_links_echo(self) -> None:
        """An echoed embed URL is not playable; the hoster keeps its video."""
        resolver = _Resolver(echo=(_VOE.url,))
        resolution, _ = _resolution(resolver)
        resolution.update([_VOE_2])
        await _settle(resolution)

        resolution.update([_VOE, _VOE_2])
        await _settle(resolution)

        assert list(_urls(resolution)) == [_VOE_2.url]
        assert resolution.videos() == 1


class TestBounds:
    async def test_each_url_resolves_once(self) -> None:
        """Two plugins list the same hoster link."""
        resolver = _Resolver(delay=0.02)
        resolution, _ = _resolution(resolver)

        resolution.update([_VOE, _VOE])
        resolution.update([_VOE, _VOE, _DOOD])
        await _settle(resolution)

        assert sorted(resolver.calls) == [_DOOD.url, _VOE.url]

    async def test_at_most_concurrency_at_a_time(self) -> None:
        resolver = _Resolver(delay=0.02)
        resolution, _ = _resolution(resolver, concurrency=2)

        resolution.update([_VOE, _DOOD, _FILEMOON])
        await _settle(resolution)

        assert resolver.most == 2
        assert len(resolver.calls) == 3

    async def test_only_the_top_streams(self) -> None:
        resolver = _Resolver()
        resolution, _ = _resolution(resolver, limit=2)

        resolution.update([_VOE, _DOOD, _FILEMOON])
        await _settle(resolution)

        assert sorted(resolver.calls) == [_DOOD.url, _VOE.url]
        assert resolution.ranked == [_VOE, _DOOD]

    async def test_a_stream_pushed_out_of_the_top_stops_resolving(self) -> None:
        """Better streams arrived: its resolution can no longer be part of
        the answer, which waited for it all the same, and it held a slot
        (code review, 2026-10-06). New results only push streams down."""

        async def resolve(url: str, hoster: str) -> ResolvedStream | None:
            if url == _DOOD.url:
                await asyncio.sleep(10)
            return _video(url)

        resolution, _ = _resolution(resolve, concurrency=1, limit=1)
        resolution.update([_DOOD])
        await asyncio.sleep(0.01)

        resolution.update([_VOE, _DOOD])
        await asyncio.wait_for(_settle(resolution), 1)

        assert list(_urls(resolution)) == [_VOE.url]


class TestProgress:
    async def test_videos_counts_hosters_with_a_video(self) -> None:
        resolver = _Resolver(echo=(_FILEMOON.url,))
        resolution, _ = _resolution(resolver)

        resolution.update([_VOE, _DOOD, _FILEMOON])
        await _settle(resolution)

        assert resolution.videos() == 2

    async def test_changed_is_set_when_a_resolution_ends(self) -> None:
        resolution, changed = _resolution(_Resolver(delay=0.01))

        resolution.update([_VOE])
        assert resolution.pending()
        await asyncio.wait_for(changed.wait(), 1)
        await _settle(resolution)

        assert resolution.videos() == 1

    async def test_cached_outcomes_with_eager_tasks(self) -> None:
        """Under eager tasks a cached resolution ends inside create_task,
        and the hoster's next link must still start."""
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            resolver = _Resolver(dead=(_VOE.url,))
            resolution, _ = _resolution(resolver)

            resolution.update([_VOE, _VOE_2])

            # Both outcomes are there without a turn of the event loop
            assert not resolution.pending()
            assert list(_urls(resolution)) == [_VOE_2.url]
        finally:
            loop.set_task_factory(None)

    async def test_aclose_cancels_what_still_runs(self) -> None:
        resolver = _Resolver(delay=10)
        resolution, _ = _resolution(resolver)
        resolution.update([_VOE, _DOOD])
        await asyncio.sleep(0.01)

        await resolution.aclose()

        assert not resolution.pending()
        assert resolution.resolved() == {}
        assert resolver.running == 0
        resolution.update([_FILEMOON])
        assert not resolution.pending()


class TestPageClaim:
    async def test_resolutions_run_under_the_claim(self) -> None:
        """The stealth browser hands its pages out by the claim."""
        seen: list[PageClaim | None] = []

        async def _resolve(url: str, hoster: str) -> ResolvedStream | None:
            seen.append(page_claim.get())
            return _video(url)

        claim = PageClaim("background", 123.0)
        resolution = HosterResolution(
            _resolve,
            concurrency=10,
            limit=50,
            changed=asyncio.Event(),
            claim=claim,
        )

        resolution.update([_VOE, _DOOD])
        await _settle(resolution)

        assert seen == [claim, claim]
        assert page_claim.get() is None


class TestLogContext:
    async def test_a_resolution_logs_its_plugin(self) -> None:
        """hoster_without_resolver names the plugin that handed out the link."""
        seen: dict[str, object] = {}

        async def _resolve(url: str, hoster: str) -> ResolvedStream | None:
            seen[url] = structlog.contextvars.get_contextvars().get("plugin")
            return _video(url)

        resolution, _ = _resolution(_resolve)

        resolution.update([replace(_VOE, source_plugin="kinoger"), _DOOD])
        await _settle(resolution)

        assert seen == {_VOE.url: "kinoger", _DOOD.url: None}
        assert "plugin" not in structlog.contextvars.get_contextvars()
