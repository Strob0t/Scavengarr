"""Tests for StremioLinks (stored stream links, resolved again when stale)."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from scavengarr.application.use_cases.stremio_links import StremioLinks
from scavengarr.domain.entities.stremio import CachedStreamLink, ResolvedStream

_OLD = "https://cdn.example/old.mp4"
_NEW = "https://cdn.example/new.mp4"


def _link(*, age: float, video_url: str = _OLD) -> CachedStreamLink:
    return CachedStreamLink(
        stream_id="sid",
        hoster_url="https://voe.sx/e/abc",
        hoster="voe",
        video_url=video_url,
        resolved_at=time.time() - age if video_url else 0.0,
    )


class _Registry:
    def __init__(self, result: ResolvedStream | None, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.calls: list[tuple[str, str, bool]] = []

    async def resolve(
        self, url: str, hoster: str = "", *, refresh: bool = False
    ) -> ResolvedStream | None:
        self.calls.append((url, hoster, refresh))
        await asyncio.sleep(self.delay)
        return self.result


def _links(
    registry: _Registry, stored: CachedStreamLink | None = None
) -> tuple[StremioLinks, AsyncMock]:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=stored)
    return StremioLinks(repo=repo, resolver=registry), repo


class TestCurrent:
    async def test_a_fresh_link_is_used_as_it_is(self) -> None:
        registry = _Registry(ResolvedStream(video_url=_NEW))
        links, repo = _links(registry)
        link = _link(age=60)

        assert await links.current(link) == link
        assert registry.calls == []
        repo.save.assert_not_awaited()

    async def test_a_stale_link_resolves_again_and_is_stored(self) -> None:
        """Autoplay plays the next episode an hour after Stremio fetched
        it, Continue Watching days later."""
        registry = _Registry(
            ResolvedStream(video_url=_NEW, headers={"Referer": "https://voe.sx/"})
        )
        links, repo = _links(registry)

        current = await links.current(_link(age=2 * 3600))

        assert current is not None
        assert current.video_url == _NEW
        assert json.loads(current.video_headers) == {"Referer": "https://voe.sx/"}
        assert time.time() - current.resolved_at < 5
        assert registry.calls == [("https://voe.sx/e/abc", "voe", False)]
        repo.save.assert_awaited_once_with(current)

    async def test_a_link_without_a_video_url_resolves(self) -> None:
        links, _ = _links(_Registry(ResolvedStream(video_url=_NEW)))

        current = await links.current(_link(age=0, video_url=""))

        assert current is not None and current.video_url == _NEW

    async def test_requests_for_one_link_share_one_resolution(self) -> None:
        """One tap on Android sent 11 requests for the stream."""
        registry = _Registry(ResolvedStream(video_url=_NEW), delay=0.05)
        links, _ = _links(registry)
        stale = _link(age=2 * 3600)

        results = await asyncio.gather(*(links.current(stale) for _ in range(5)))

        assert {r.video_url for r in results if r} == {_NEW}
        assert len(registry.calls) == 1

    async def test_a_failed_resolution_keeps_the_stale_video_url(self) -> None:
        """The hoster can fail while its CDN still serves the stored URL:
        3 FireStream links of the dev-server end-to-end run resolved no
        more after an hour, their stored playlists still played."""
        links, repo = _links(_Registry(None))
        stale = _link(age=2 * 3600)

        assert await links.current(stale) == stale
        repo.save.assert_not_awaited()

    async def test_without_a_video_url_a_failed_resolution_gives_none(self) -> None:
        links, repo = _links(_Registry(None))

        assert await links.current(_link(age=0, video_url="")) is None
        repo.save.assert_not_awaited()

    async def test_an_echoed_embed_url_gives_none(self) -> None:
        links, _ = _links(_Registry(ResolvedStream(video_url="https://voe.sx/e/abc")))

        assert await links.current(_link(age=0, video_url="")) is None

    async def test_a_failed_save_still_plays(self) -> None:
        links, repo = _links(_Registry(ResolvedStream(video_url=_NEW)))
        repo.save = AsyncMock(side_effect=ConnectionError("redis down"))

        current = await links.current(_link(age=2 * 3600))

        assert current is not None and current.video_url == _NEW


class TestRefreshed:
    async def test_resolves_past_the_resolver_cache(self) -> None:
        """The CDN refused the stored stream (403, 404, 410)."""
        registry = _Registry(ResolvedStream(video_url=_NEW))
        links, _ = _links(registry)

        current = await links.refreshed(_link(age=60))

        assert current is not None and current.video_url == _NEW
        assert registry.calls == [("https://voe.sx/e/abc", "voe", True)]

    async def test_a_failed_resolution_gives_none(self) -> None:
        """The CDN refused the stored URL: there is nothing to fall back to."""
        links, _ = _links(_Registry(None))

        assert await links.refreshed(_link(age=60)) is None


class TestGet:
    async def test_looks_up_the_stored_link(self) -> None:
        stored = _link(age=60)
        links, repo = _links(_Registry(None), stored)

        assert await links.get("sid") == stored
        repo.get.assert_awaited_once_with("sid")


class TestShutdown:
    async def test_aclose_ends_running_resolutions(self) -> None:
        registry = _Registry(ResolvedStream(video_url=_NEW), delay=30)
        links, _ = _links(registry)
        request = asyncio.create_task(links.current(_link(age=2 * 3600)))
        await asyncio.sleep(0.01)

        await links.aclose()

        with pytest.raises(asyncio.CancelledError):
            await request
