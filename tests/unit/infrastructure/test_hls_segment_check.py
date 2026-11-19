"""Tests for the HLS segment probe of hosters whose CDN links expire."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.hoster_resolvers import _verify
from scavengarr.infrastructure.hoster_resolvers._verify import segment_is_gone
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.hoster_resolvers.strmup import StrmupResolver
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

_EMBED = "https://strmup.to/abc1234567890"
_MASTER = "https://cdn.example.com/hls/abc/master.m3u8?t=tok"
_VARIANT = "https://cdn.example.com/hls/abc/720/index.m3u8?t=tok"
_SEGMENT = "https://cdn.example.com/hls/abc/720/seg-1.ts?t=tok"

_MASTER_BODY = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
    "720/index.m3u8?t=tok\n"
)
_MEDIA_BODY = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:6\n"
    "#EXTINF:6.0,\nseg-1.ts?t=tok\n#EXTINF:6.0,\nseg-2.ts?t=tok\n#EXT-X-ENDLIST\n"
)
_PAGE = (
    f'<html><body>{"x" * 120}<script>streaming_url: "{_MASTER}"</script></body></html>'
)


def _cdn(segment_status: int = 200, **segment: object) -> respx.Route:
    """Mock the page, the playlists and the first segment of one stream."""
    respx.get(_EMBED).respond(200, text=_PAGE)
    respx.get(_MASTER).respond(200, text=_MASTER_BODY)
    respx.get(_VARIANT).respond(200, text=_MEDIA_BODY)
    route = respx.get(_SEGMENT)
    if "side_effect" in segment:
        route.mock(side_effect=segment["side_effect"])
    else:
        route.respond(segment_status, content=b"\x47" * 188)
    return route


async def _resolve(
    *, verify_hls_segment: bool = True, verify_playback: bool = False
) -> ResolvedStream | None:
    async with httpx.AsyncClient() as client:
        registry = HosterResolverRegistry(
            resolvers=[StrmupResolver(http_client=client)],
            http_client=client,
            verify_playback=verify_playback,
            verify_hls_segment=verify_hls_segment,
        )
        return await registry.resolve(_EMBED)


class TestSegmentProbeInRegistry:
    @pytest.mark.parametrize("status", [404, 403, 410])
    @respx.mock
    async def test_a_gone_segment_drops_the_stream(self, status: int) -> None:
        _cdn(status)
        with structlog.testing.capture_logs() as logs:
            assert await _resolve() is None
        assert any(e["event"] == "hoster_resolve_unplayable" for e in logs)

    @respx.mock
    async def test_a_gone_segment_drops_the_stream_after_the_playback_check(
        self,
    ) -> None:
        _cdn(404)
        assert await _resolve(verify_playback=True) is None

    @respx.mock
    async def test_a_segment_that_answers_keeps_the_stream(self) -> None:
        route = _cdn(200)
        stream = await _resolve()
        assert stream is not None
        assert stream.video_url == _MASTER
        assert route.call_count == 1

    @respx.mock
    async def test_a_range_answer_keeps_the_stream(self) -> None:
        _cdn(206)
        assert await _resolve() is not None

    @respx.mock
    async def test_a_server_error_keeps_the_stream(self) -> None:
        _cdn(500)
        assert await _resolve() is not None

    @respx.mock
    async def test_a_timeout_keeps_the_stream(self) -> None:
        _cdn(side_effect=httpx.ReadTimeout("slow"))
        assert await _resolve() is not None

    @respx.mock
    async def test_a_network_error_keeps_the_stream(self) -> None:
        _cdn(side_effect=httpx.ConnectError("refused"))
        assert await _resolve() is not None

    @respx.mock
    async def test_the_setting_off_asks_for_no_segment(self) -> None:
        route = _cdn(404)
        assert await _resolve(verify_hls_segment=False) is not None
        assert route.call_count == 0

    @respx.mock
    async def test_a_hoster_without_the_probe_is_untouched(self) -> None:
        """Another resolver's HLS stream is never asked for a segment."""
        route = respx.get(_SEGMENT).respond(404)
        master = respx.get(_MASTER).respond(200, text=_MASTER_BODY)

        class Other:
            name = "other"
            supported_domains = frozenset({"other"})

            async def resolve(self, url: str) -> ResolvedStream | None:
                return ResolvedStream(video_url=_MASTER, is_hls=True)

        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[Other()],
                http_client=client,
                verify_hls_segment=True,
            )
            stream = await registry.resolve("https://other.to/abc1234567890")
        assert stream is not None
        assert route.call_count == 0
        assert master.call_count == 0


class TestSegmentIsGone:
    @respx.mock
    async def test_walks_master_variant_and_segment_with_the_playback_headers(
        self,
    ) -> None:
        respx.get(_MASTER).respond(200, text=_MASTER_BODY)
        respx.get(_VARIANT).respond(200, text=_MEDIA_BODY)
        route = respx.get(_SEGMENT).respond(206, content=b"\x47")
        stream = ResolvedStream(
            video_url=_MASTER, is_hls=True, headers={"Referer": "https://strmup.to/"}
        )
        async with httpx.AsyncClient() as client:
            assert await segment_is_gone(client, stream) is False
        sent = route.calls.last.request
        assert sent.headers["Referer"] == "https://strmup.to/"
        assert sent.headers["User-Agent"] == DEFAULT_USER_AGENT
        assert sent.headers["Range"].startswith("bytes=0-")

    @respx.mock
    async def test_a_media_playlist_needs_no_variant_step(self) -> None:
        respx.get(_VARIANT).respond(200, text=_MEDIA_BODY)
        respx.get(_SEGMENT).respond(404)
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url=_VARIANT, is_hls=True)
            assert await segment_is_gone(client, stream) is True

    @respx.mock
    async def test_an_absolute_segment_url_is_used_as_listed(self) -> None:
        other = "https://seg.example.net/a/1.ts"
        respx.get(_VARIANT).respond(
            200, text=f"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\n{other}\n"
        )
        route = respx.get(other).respond(403)
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url=_VARIANT, is_hls=True)
            assert await segment_is_gone(client, stream) is True
        assert route.call_count == 1

    @respx.mock
    async def test_a_segment_without_a_query_gets_the_stream_urls(self) -> None:
        """The proxy sends the stream URL's token to such a segment."""
        respx.get(_VARIANT).respond(
            200, text="#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg-1.ts\n"
        )
        route = respx.get(_SEGMENT).respond(404)
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url=_VARIANT, is_hls=True)
            assert await segment_is_gone(client, stream) is True
        assert route.call_count == 1

    @respx.mock
    async def test_a_gone_playlist_is_not_a_verdict_on_the_segment(self) -> None:
        respx.get(_MASTER).respond(404)
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url=_MASTER, is_hls=True)
            assert await segment_is_gone(client, stream) is False

    @respx.mock
    async def test_a_playlist_without_a_segment_is_kept(self) -> None:
        respx.get(_VARIANT).respond(200, text="#EXTM3U\n#EXT-X-ENDLIST\n")
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url=_VARIANT, is_hls=True)
            assert await segment_is_gone(client, stream) is False

    @respx.mock
    async def test_a_file_is_not_probed(self) -> None:
        route = respx.get("https://cdn.example.com/v.mp4").respond(404)
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url="https://cdn.example.com/v.mp4")
            assert await segment_is_gone(client, stream) is False
        assert route.call_count == 0

    @respx.mock
    async def test_the_walk_ends_at_the_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A slow CDN keeps the stream, and the probe returns at the bound."""
        monkeypatch.setattr(_verify, "_SEGMENT_PROBE_TIMEOUT_S", 0.2)

        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(404)

        respx.get(_MASTER).mock(side_effect=slow)
        async with httpx.AsyncClient() as client:
            stream = ResolvedStream(video_url=_MASTER, is_hls=True)
            started = time.monotonic()
            assert await segment_is_gone(client, stream) is False
        assert time.monotonic() - started < 2

    def test_the_bound_is_four_seconds(self) -> None:
        assert _verify._SEGMENT_PROBE_TIMEOUT_S == 4.0
