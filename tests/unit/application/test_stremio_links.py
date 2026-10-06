"""Tests for StremioLinks (stored stream links, resolved again when stale)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
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
    def __init__(
        self,
        result: ResolvedStream | None,
        delay: float = 0.0,
        *,
        bound: tuple[str, ...] = (),
        for_player: ResolvedStream | None = None,
    ) -> None:
        self.result = result
        self.delay = delay
        self.bound = bound
        self.for_player = for_player
        self.calls: list[tuple[str, str, bool]] = []
        self.player_calls: list[tuple[str, str, dict[str, str]]] = []

    async def resolve(
        self, url: str, hoster: str = "", *, refresh: bool = False
    ) -> ResolvedStream | None:
        self.calls.append((url, hoster, refresh))
        await asyncio.sleep(self.delay)
        return self.result

    def bound_headers(self, url: str, hoster: str = "") -> tuple[str, ...]:
        return self.bound

    async def resolve_for_client(
        self, url: str, hoster: str, headers: Mapping[str, str]
    ) -> ResolvedStream | None:
        self.player_calls.append((url, hoster, dict(headers)))
        await asyncio.sleep(self.delay)
        return self.for_player


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

    async def test_a_resolution_from_the_cache_keeps_its_time(self) -> None:
        """The registry's cache answers for an hour: stamped "now", its
        video URL counted as fresh for another hour (code review,
        2026-10-06)."""
        half_an_hour_ago = time.time() - 1800
        links, _ = _links(
            _Registry(ResolvedStream(video_url=_NEW, resolved_at=half_an_hour_ago))
        )

        current = await links.current(_link(age=2 * 3600))

        assert current is not None and current.resolved_at == half_an_hour_ago

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


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
_PLAYER_URL = "https://cdn.example/player.mp4"
_FIREFOX = {"user-agent": _UA, "accept-language": "de", "referer": "https://s.lan/"}


def _veev_link(*, age: float = 60) -> CachedStreamLink:
    return CachedStreamLink(
        stream_id="sid",
        hoster_url="https://veev.to/e/abc",
        hoster="veev",
        video_url=_OLD,
        video_headers=json.dumps({"Referer": "https://veev.to/", "User-Agent": _UA}),
        resolved_at=time.time() - age,
    )


def _veev_registry(delay: float = 0.0, *, plays: bool = True) -> _Registry:
    return _Registry(
        ResolvedStream(video_url=_NEW),
        delay,
        bound=("user-agent", "accept-language"),
        for_player=(
            ResolvedStream(
                video_url=_PLAYER_URL,
                headers={"User-Agent": _UA, "Accept-Language": "de"},
            )
            if plays
            else None
        ),
    )


class TestPlayerBoundHosters:
    """veevcdn binds the video URL to the User-Agent and the Accept-Language
    of the resolution; Stremio's streaming server passes the browser's
    Accept-Language on, and the stored URL answered it 403 (production,
    2026-10-06)."""

    async def test_a_player_with_other_bound_headers_gets_its_own_link(
        self,
    ) -> None:
        registry = _veev_registry()
        links, repo = _links(registry)

        current = await links.current(_veev_link(), _FIREFOX)

        assert current is not None
        assert current.video_url == _PLAYER_URL
        assert current.stream_id.startswith("sid-")
        assert json.loads(current.video_headers)["Accept-Language"] == "de"
        assert registry.player_calls == [
            (
                "https://veev.to/e/abc",
                "veev",
                {"user-agent": _UA, "accept-language": "de"},
            )
        ]
        assert registry.calls == []
        repo.save.assert_awaited_once_with(current)

    async def test_a_player_sending_the_stored_headers_gets_the_stored_link(
        self,
    ) -> None:
        """ffmpeg's probe through Stremio's proxy sends our User-Agent and
        no Accept-Language: the URL resolved for the answer plays."""
        registry = _veev_registry()
        links, _ = _links(registry)
        link = _veev_link()

        assert await links.current(link, {"User-Agent": _UA}) == link
        assert registry.player_calls == []

    async def test_the_players_link_is_used_while_fresh(self) -> None:
        own = CachedStreamLink(
            stream_id="sid-own",
            hoster_url="https://veev.to/e/abc",
            hoster="veev",
            video_url=_PLAYER_URL,
            resolved_at=time.time() - 60,
        )
        registry = _veev_registry()
        links, repo = _links(registry, own)

        assert await links.current(_veev_link(), _FIREFOX) == own
        assert registry.player_calls == []
        assert repo.get.await_args.args[0].startswith("sid-")

    async def test_a_stale_players_link_resolves_again(self) -> None:
        own = CachedStreamLink(
            stream_id="sid-own",
            hoster_url="https://veev.to/e/abc",
            hoster="veev",
            video_url=_OLD,
            resolved_at=time.time() - 2 * 3600,
        )
        registry = _veev_registry()
        links, _ = _links(registry, own)

        current = await links.current(_veev_link(), _FIREFOX)

        assert current is not None and current.video_url == _PLAYER_URL
        assert len(registry.player_calls) == 1

    async def test_requests_of_one_player_share_one_resolution(self) -> None:
        """Firefox sends a HEAD and a GET through Stremio's proxy."""
        registry = _veev_registry(delay=0.05)
        links, _ = _links(registry)

        results = await asyncio.gather(
            *(links.current(_veev_link(), _FIREFOX) for _ in range(3))
        )

        assert {r.video_url for r in results if r} == {_PLAYER_URL}
        assert len(registry.player_calls) == 1

    async def test_two_players_get_a_link_each(self) -> None:
        registry = _veev_registry()
        links, _ = _links(registry)

        german = await links.current(_veev_link(), _FIREFOX)
        english = await links.current(
            _veev_link(), {**_FIREFOX, "accept-language": "en-US,en;q=0.5"}
        )

        assert german is not None and english is not None
        assert german.stream_id != english.stream_id
        assert len(registry.player_calls) == 2

    async def test_a_failed_player_resolution_plays_the_stored_link(self) -> None:
        links, repo = _links(_veev_registry(plays=False))
        link = _veev_link()

        assert await links.current(link, _FIREFOX) == link
        repo.save.assert_not_awaited()

    async def test_other_hosters_ignore_the_players_headers(self) -> None:
        registry = _Registry(ResolvedStream(video_url=_NEW))
        links, _ = _links(registry)
        link = _link(age=60)

        assert await links.current(link, _FIREFOX) == link
        assert registry.player_calls == []


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
