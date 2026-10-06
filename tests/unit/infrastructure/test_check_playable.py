"""Tests for the playback check applied to resolved stream URLs."""

from __future__ import annotations

import httpx
import pytest
import respx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers._verify import (
    _SNIFF_BYTES,
    PlaybackCheck,
    check_playable,
)
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

_MP4 = "https://cdn.example.com/v.mp4"
_M3U8 = "https://cdn.example.com/master.m3u8"


async def _measure(stream: ResolvedStream) -> PlaybackCheck:
    async with httpx.AsyncClient() as client:
        return await check_playable(client, stream)


async def _check(stream: ResolvedStream) -> bool:
    return (await _measure(stream)).playable


_MASTER = (
    b"#EXTM3U\n"
    b"#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480\nlow/index.m3u8\n"
    b'#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1"\n'
    b"high/index.m3u8\n"
    b"#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\nmid/index.m3u8\n"
)


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
    async def test_a_failed_check_logs_the_cdn_not_its_url(self) -> None:
        """CDN URLs carry tokens and the client's address (code review,
        2026-10-06)."""
        url = "https://cdn.example.com/secure/secret-token/v.mp4?i=1.2.3.4"
        respx.get(url).respond(403)

        with structlog.testing.capture_logs() as logs:
            await _check(ResolvedStream(url))

        failed = [e for e in logs if e["event"] == "playback_check_failed"]
        assert failed and failed[0]["cdn"] == "example"
        assert not any("secret-token" in str(v) for v in failed[0].values())

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

    @respx.mock
    async def test_the_result_carries_the_measurement(self) -> None:
        """A resolver result without a quality gets the master playlist's,
        and the cache answers with it."""

        class _Hls:
            name = "voe"

            async def resolve(self, url: str) -> ResolvedStream | None:
                return ResolvedStream(_M3U8, is_hls=True)

        respx.get(_M3U8).respond(200, content=_MASTER)
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_Hls()], http_client=client, verify_playback=True
            )
            result = await registry.resolve("https://voe.sx/e/1")

        assert result is not None
        assert result.quality is StreamQuality.HD_1080P
        assert registry.cached("https://voe.sx/e/1") == (True, result)

    @respx.mock
    async def test_the_result_carries_the_file_size(self) -> None:
        respx.get(_MP4).respond(
            206,
            content=b"\x1aE\xdf\xa3",
            headers={"Content-Range": "bytes 0-4095/1500000000"},
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_StubResolver()], http_client=client, verify_playback=True
            )
            result = await registry.resolve("https://voe.sx/e/1")

        assert result is not None
        assert result.size_bytes == 1_500_000_000
        assert result.quality is StreamQuality.UNKNOWN

    @respx.mock
    async def test_a_better_quality_of_the_resolver_stays(self) -> None:
        class _Uhd:
            name = "voe"

            async def resolve(self, url: str) -> ResolvedStream | None:
                return ResolvedStream(_M3U8, is_hls=True, quality=StreamQuality.UHD_4K)

        respx.get(_M3U8).respond(200, content=_MASTER)
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_Uhd()], http_client=client, verify_playback=True
            )
            result = await registry.resolve("https://voe.sx/e/1")

        assert result is not None
        assert result.quality is StreamQuality.UHD_4K


class TestMeasurement:
    """What the check reads from the bytes it fetches anyway."""

    @respx.mock
    async def test_a_master_playlist_gives_its_largest_variant(self) -> None:
        respx.get(_M3U8).respond(200, content=_MASTER)

        check = await _measure(ResolvedStream(_M3U8, is_hls=True))

        assert check == PlaybackCheck(playable=True, width=1920, height=1080)

    @respx.mock
    async def test_a_media_playlist_has_no_resolution(self) -> None:
        respx.get(_M3U8).respond(
            200, content=b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n"
        )

        check = await _measure(ResolvedStream(_M3U8, is_hls=True))

        assert check == PlaybackCheck(playable=True)

    @respx.mock
    async def test_a_variant_cut_off_by_the_sniff_is_not_read(self) -> None:
        # The check reads 4 KiB: a 4K variant whose height is cut must not
        # count as 3840x21
        head = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1280x720\na\n"
        cut = b"#EXT-X-STREAM-INF:BANDWIDTH=9,RESOLUTION=3840x21"
        padding = b"#" * (_SNIFF_BYTES - len(head) - len(cut) - 1) + b"\n"
        respx.get(_M3U8).respond(200, content=head + padding + cut + b"60\nb\n")

        check = await _measure(ResolvedStream(_M3U8, is_hls=True))

        assert (check.width, check.height) == (1280, 720)

    @respx.mock
    async def test_a_file_gives_its_size(self) -> None:
        respx.get(_MP4).respond(
            206,
            content=b"\x00\x00\x00\x18ftypmp42",
            headers={"Content-Range": "bytes 0-4095/1500000000"},
        )

        check = await _measure(ResolvedStream(_MP4))

        assert check == PlaybackCheck(playable=True, size_bytes=1_500_000_000)

    @respx.mock
    @pytest.mark.parametrize("content_range", ["bytes 0-4095/*", None])
    async def test_an_unknown_total_gives_no_size(
        self, content_range: str | None
    ) -> None:
        headers = {"Content-Range": content_range} if content_range else {}
        respx.get(_MP4).respond(206, content=b"\x00\x00\x00\x18", headers=headers)

        check = await _measure(ResolvedStream(_MP4))

        assert check.size_bytes is None

    @respx.mock
    async def test_a_refused_stream_has_no_measurement(self) -> None:
        respx.get(_MP4).respond(
            403, headers={"Content-Range": "bytes 0-4095/1500000000"}
        )

        assert await _measure(ResolvedStream(_MP4)) == PlaybackCheck(playable=False)

    @respx.mock
    async def test_the_check_asks_for_the_sniff(self) -> None:
        route = respx.get(_MP4).respond(206, content=b"\x00\x00\x00\x18")

        await _measure(ResolvedStream(_MP4))

        assert route.calls.last.request.headers["Range"] == (
            f"bytes=0-{_SNIFF_BYTES - 1}"
        )
        assert _SNIFF_BYTES == 4096
