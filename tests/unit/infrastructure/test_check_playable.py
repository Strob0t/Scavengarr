"""Tests for the playback check applied to resolved stream URLs."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers._verify import check_playable
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

_MP4 = "https://cdn.example.com/v.mp4"
_M3U8 = "https://cdn.example.com/master.m3u8"


async def _check(stream: ResolvedStream) -> bool:
    async with httpx.AsyncClient() as client:
        return await check_playable(client, stream)


class TestCheckPlayable:
    @respx.mock
    async def test_video_bytes_are_playable(self) -> None:
        route = respx.get(_MP4).respond(206, content=b"\x00\x00\x00\x18ftypmp42")
        assert await _check(ResolvedStream(_MP4, headers={"Referer": "https://h/"}))
        sent = route.calls.last.request
        assert sent.headers["Referer"] == "https://h/"
        assert sent.headers["Range"].startswith("bytes=0-")

    @respx.mock
    async def test_sends_the_players_user_agent(self) -> None:
        # Stremio plays with the browser User-Agent of the proxyHeaders;
        # mixdrop's CDN answers other agents with 403, so the check dropped
        # streams the player could play
        route = respx.get(_MP4).respond(206, content=b"\x00\x00\x00\x18ftypmp42")
        assert await _check(ResolvedStream(_MP4))
        assert route.calls.last.request.headers["User-Agent"] == DEFAULT_USER_AGENT

    @respx.mock
    async def test_stream_user_agent_wins(self) -> None:
        route = respx.get(_MP4).respond(206, content=b"\x00\x00\x00\x18ftypmp42")
        assert await _check(ResolvedStream(_MP4, headers={"User-Agent": "Own/1"}))
        assert route.calls.last.request.headers["User-Agent"] == "Own/1"

    @respx.mock
    @pytest.mark.parametrize("status", [403, 404, 502])
    async def test_error_status_is_not_playable(self, status: int) -> None:
        respx.get(_MP4).respond(status)
        assert not await _check(ResolvedStream(_MP4))

    @respx.mock
    async def test_html_page_is_not_playable(self) -> None:
        respx.get(_MP4).respond(
            200,
            content=b"<!DOCTYPE html><html>",
            headers={"Content-Type": "text/html"},
        )
        assert not await _check(ResolvedStream(_MP4))

    @respx.mock
    async def test_html_without_content_type_is_not_playable(self) -> None:
        respx.get(_MP4).respond(200, content=b"  <html><body>File not found")
        assert not await _check(ResolvedStream(_MP4))

    @respx.mock
    async def test_hls_playlist_is_playable(self) -> None:
        respx.get(_M3U8).respond(200, content=b"#EXTM3U\n#EXT-X-VERSION:3\n")
        assert await _check(ResolvedStream(_M3U8, is_hls=True))

    @respx.mock
    async def test_hls_without_playlist_is_not_playable(self) -> None:
        respx.get(_M3U8).respond(200, content=b"forbidden")
        assert not await _check(ResolvedStream(_M3U8, is_hls=True))

    @respx.mock
    async def test_a_network_error_is_raised(self) -> None:
        """It says nothing about the stream; the registry decides."""
        respx.get(_MP4).mock(side_effect=httpx.ConnectError("down"))

        with pytest.raises(httpx.ConnectError):
            await _check(ResolvedStream(_MP4))


class _StubResolver:
    name = "voe"

    async def resolve(self, url: str) -> ResolvedStream | None:
        return ResolvedStream(_MP4)


class TestRegistryVerifiesPlayback:
    @respx.mock
    async def test_unplayable_result_is_dropped_and_cached(self) -> None:
        route = respx.get(_MP4).respond(502)
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_StubResolver()], http_client=client, verify_playback=True
            )
            assert await registry.resolve("https://voe.sx/e/1") is None
            assert await registry.resolve("https://voe.sx/e/1") is None
        assert route.call_count == 1

    @respx.mock
    async def test_a_network_error_in_the_check_is_neither_cached_nor_counted(
        self,
    ) -> None:
        """A timeout of the first-KiB check on a loaded Pi cached a working
        link as dead for 900 s and counted against the hoster, while the same
        error from the resolver did neither (code review, 2026-10-06)."""
        respx.get(_MP4).mock(
            side_effect=[
                httpx.ReadTimeout("slow"),
                httpx.Response(206, content=b"\x1aE\xdf\xa3"),
            ]
        )
        breaker = PluginCircuitBreaker(failure_threshold=1)
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_StubResolver()],
                http_client=client,
                verify_playback=True,
                circuit_breaker=breaker,
            )

            assert await registry.resolve("https://voe.sx/e/1") is None
            assert breaker.is_closed("voe")
            assert await registry.resolve("https://voe.sx/e/1") is not None

    @respx.mock
    async def test_playable_result_is_returned(self) -> None:
        respx.get(_MP4).respond(206, content=b"\x1aE\xdf\xa3")
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_StubResolver()], http_client=client, verify_playback=True
            )
            result = await registry.resolve("https://voe.sx/e/1")
        assert result is not None
        assert result.video_url == _MP4

    async def test_no_check_by_default(self) -> None:
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_StubResolver()], http_client=client
            )
            # respx is not active: a real request would fail the check
            assert await registry.resolve("https://voe.sx/e/1") is not None
