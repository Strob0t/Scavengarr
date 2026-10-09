"""Tests for the HLS proxy's read-ahead: the segments after the one the
player asks for, fetched into memory (``SegmentReadAhead``)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from structlog.testing import capture_logs

from scavengarr.infrastructure.stremio.hls_proxy import (
    SegmentReadAhead,
    listed_segments,
    stream_hls_segment,
)
from scavengarr.infrastructure.telemetry import Telemetry

_CDN = "https://cdn.test/v/"
_PLAYLIST = f"{_CDN}720p.m3u8?t=abc"
_SEGMENTS = [f"{_CDN}seg-{i}.ts?t=abc" for i in range(1, 7)]


def _segment_body(url: str, size: int = 4096) -> bytes:
    """A body of *size* bytes naming its segment."""
    name = url.rsplit("/", 1)[-1].split("?")[0].encode()
    return (name + b"|") * (size // (len(name) + 1) + 1)


class _Cdn:
    """A CDN answering each segment with its own bytes; counts the requests.

    *statuses* answers the n-th request for a URL with a status other than
    200 (``{"seg-2.ts": [429]}``: the first request for seg-2 gets 429).
    """

    def __init__(
        self,
        *,
        size: int = 4096,
        statuses: dict[str, list[int]] | None = None,
        declare_length: bool = True,
    ) -> None:
        self.requests: list[str] = []
        self.size = size
        self.statuses = statuses or {}
        self.declare_length = declare_length
        self.block: asyncio.Event | None = None

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    def requested(self, name: str) -> int:
        return sum(
            1 for url in self.requests if f"/{name}?" in url or url.endswith(name)
        )

    async def _answer(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if self.block is not None:
            await self.block.wait()
        name = request.url.path.rsplit("/", 1)[-1]
        queued = self.statuses.get(name)
        if queued:
            return httpx.Response(queued.pop(0))
        body = _segment_body(url, self.size)
        if self.declare_length:
            return httpx.Response(
                200, content=body, headers={"content-type": "video/mp2t"}
            )
        return httpx.Response(
            200, stream=_Chunks([body]), headers={"content-type": "video/mp2t"}
        )


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _count(telemetry: Telemetry, outcome: str) -> float:
    return (
        telemetry.registry.get_sample_value(
            "scavengarr_hls_readahead_total", {"outcome": outcome}
        )
        or 0.0
    )


async def _settle(read_ahead: SegmentReadAhead) -> None:
    """Wait for every fetch ahead in flight."""
    tasks = [t for p in read_ahead._playbacks.values() for t in p.ahead.values()]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _segment(
    client: httpx.AsyncClient, read_ahead: SegmentReadAhead, url: str
) -> tuple[bytes, str]:
    """A segment through the proxy's path, as the player gets it."""
    chunks, content_type = await stream_hls_segment(
        client, url, {}, read_ahead=read_ahead
    )
    return b"".join([chunk async for chunk in chunks]), content_type


class TestReadAhead:
    async def test_the_next_two_segments_are_fetched_ahead_and_served(self) -> None:
        cdn = _Cdn()
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            first, _ = await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            second, content_type = await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)

        assert first == _segment_body(_SEGMENTS[0])
        assert (second, content_type) == (_segment_body(_SEGMENTS[1]), "video/mp2t")
        # seg-2 and seg-3 after the first request, seg-4 after the second
        assert [u.rsplit("/", 1)[-1] for u in cdn.requests] == [
            "seg-1.ts?t=abc",
            "seg-2.ts?t=abc",
            "seg-3.ts?t=abc",
            "seg-4.ts?t=abc",
        ]
        assert (_count(telemetry, "miss"), _count(telemetry, "hit")) == (1, 1)
        assert _count(telemetry, "ok") == 3
        assert (
            telemetry.registry.get_sample_value(
                "scavengarr_hls_readahead_seconds_count", {}
            )
            == 3
        )

    async def test_a_fetch_in_flight_is_awaited(self) -> None:
        cdn = _Cdn()
        cdn.block = asyncio.Event()
        read_ahead = SegmentReadAhead()
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            cdn.block.set()
            await _segment(client, read_ahead, _SEGMENTS[0])
            cdn.block.clear()
            waiting = asyncio.create_task(_segment(client, read_ahead, _SEGMENTS[1]))
            await asyncio.sleep(0.01)
            assert not waiting.done()
            cdn.block.set()
            body, _ = await waiting
            await _settle(read_ahead)

        assert body == _segment_body(_SEGMENTS[1])
        assert cdn.requested("seg-2.ts") == 1

    async def test_at_most_two_segments_ahead(self) -> None:
        cdn = _Cdn()
        read_ahead = SegmentReadAhead()
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            for url in _SEGMENTS[:4]:
                await _segment(client, read_ahead, url)
                await _settle(read_ahead)
                playback = read_ahead._playbacks[_PLAYLIST]
                assert len(playback.ahead) <= 2
            await _settle(read_ahead)

        # every segment fetched once, up to the two after the fourth
        assert sorted(cdn.requests) == sorted(_SEGMENTS)

    async def test_a_seek_drops_the_fetches_ahead(self) -> None:
        cdn = _Cdn()
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            await _segment(client, read_ahead, _SEGMENTS[4])
            await _settle(read_ahead)
            playback = read_ahead._playbacks[_PLAYLIST]
            assert list(playback.ahead) == [_SEGMENTS[5]]
            # back again: seg-2 was dropped and is fetched once more
            body, _ = await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)

        assert body == _segment_body(_SEGMENTS[1])
        assert cdn.requested("seg-2.ts") == 2
        assert _count(telemetry, "dropped") == 3  # seg-2, seg-3, then seg-6

    async def test_a_head_request_touches_nothing(self) -> None:
        cdn = _Cdn()
        read_ahead = SegmentReadAhead()
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            chunks, _ = await stream_hls_segment(
                client, _SEGMENTS[0], {}, head=True, read_ahead=read_ahead
            )
            assert [chunk async for chunk in chunks] == []
            await _settle(read_ahead)

        assert cdn.requested("seg-1.ts") == 1
        assert not read_ahead._playbacks[_PLAYLIST].ahead

    async def test_a_segment_of_no_playlist_is_streamed_as_before(self) -> None:
        cdn = _Cdn()
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry)
        async with cdn.client() as client:
            body, _ = await _segment(client, read_ahead, _SEGMENTS[0])

        assert body == _segment_body(_SEGMENTS[0])
        assert cdn.requests == [_SEGMENTS[0]]
        assert _count(telemetry, "miss") == 0

    async def test_served_from_memory_in_64_kib_pieces_and_counted(self) -> None:
        cdn = _Cdn(size=150_000)
        read_ahead = SegmentReadAhead()
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        sent: list[int] = []
        async with cdn.client() as client:
            await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            chunks, _ = await stream_hls_segment(
                client, _SEGMENTS[1], {}, on_sent=sent.append, read_ahead=read_ahead
            )
            received = [chunk async for chunk in chunks]
            await _settle(read_ahead)

        body = _segment_body(_SEGMENTS[1], 150_000)
        assert [len(chunk) for chunk in received] == [65536, 65536, len(body) - 131072]
        assert b"".join(received) == body
        assert sent == [len(body)]


class TestBounds:
    async def test_a_segment_over_the_cap_is_left_to_the_player(self) -> None:
        cdn = _Cdn(size=10_000)
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry, max_bytes=8_000)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            body, _ = await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)

        assert body == _segment_body(_SEGMENTS[1], 10_000)
        assert cdn.requested("seg-2.ts") == 2
        # seg-2 and seg-3 after the first request, seg-4 after the second:
        # a declared size costs the headers only, the playback goes on
        assert _count(telemetry, "too_large") == 3
        assert _count(telemetry, "miss") == 2
        assert cdn.requested("seg-4.ts") == 1

    async def test_an_undeclared_body_over_the_cap_is_cut(self) -> None:
        cdn = _Cdn(size=10_000, declare_length=False)
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry, max_bytes=8_000)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)

        assert _count(telemetry, "too_large") == 2
        # the cap's bytes were read for nothing: no further fetches ahead
        assert cdn.requested("seg-4.ts") == 0

    async def test_a_429_turns_read_ahead_off_for_the_host(self) -> None:
        cdn = _Cdn(statuses={"seg-2.ts": [429]})
        telemetry = Telemetry()
        clock = _Clock()
        read_ahead = SegmentReadAhead(telemetry=telemetry, clock=clock)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            with capture_logs() as logs:
                await _segment(client, read_ahead, _SEGMENTS[0])
                await _settle(read_ahead)
            # the player's own request for seg-2 gets the CDN's second answer
            await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)
            requests_while_off = len(cdn.requests)
            clock.now += 601
            await _segment(client, read_ahead, _SEGMENTS[2])
            await _settle(read_ahead)

        assert _count(telemetry, "throttled") == 1
        throttled = next(e for e in logs if e["event"] == "hls_readahead_throttled")
        assert throttled["cdn"] == "cdn"
        assert "http" not in str(throttled)
        # seg-1, seg-2 (429), seg-3 ahead; then seg-2 by the player, nothing ahead
        assert requests_while_off == 4
        # on again after 10 minutes: seg-3 by the player, seg-4 and seg-5 ahead
        assert cdn.requested("seg-4.ts") == 1 and cdn.requested("seg-5.ts") == 1

    async def test_an_idle_playback_is_dropped(self) -> None:
        cdn = _Cdn()
        telemetry = Telemetry()
        clock = _Clock()
        read_ahead = SegmentReadAhead(telemetry=telemetry, clock=clock)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        other = [f"{_CDN}other-{i}.ts" for i in range(1, 4)]
        async with cdn.client() as client:
            await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            clock.now += 61
            read_ahead.remember(f"{_CDN}other.m3u8", other)

        assert _PLAYLIST not in read_ahead._playbacks
        assert _count(telemetry, "dropped") == 2
        assert read_ahead._playlist_of.get(_SEGMENTS[0]) is None

    async def test_at_most_eight_playbacks(self) -> None:
        clock = _Clock()
        read_ahead = SegmentReadAhead(clock=clock)
        for i in range(9):
            clock.now += 1
            read_ahead.remember(f"{_CDN}p{i}.m3u8", [f"{_CDN}p{i}-seg-1.ts"])

        assert len(read_ahead._playbacks) == 8
        assert f"{_CDN}p0.m3u8" not in read_ahead._playbacks
        assert f"{_CDN}p8.m3u8" in read_ahead._playbacks

    async def test_a_playlist_fetched_again_keeps_the_segments_still_listed(
        self,
    ) -> None:
        cdn = _Cdn()
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry)
        read_ahead.remember(_PLAYLIST, _SEGMENTS[:4])
        async with cdn.client() as client:
            await _segment(client, read_ahead, _SEGMENTS[0])
            await _settle(read_ahead)
            # a live playlist moved on: seg-1 gone, seg-5 new
            read_ahead.remember(_PLAYLIST, _SEGMENTS[1:5])
            body, _ = await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)

        assert body == _segment_body(_SEGMENTS[1])
        assert cdn.requested("seg-2.ts") == 1
        assert _count(telemetry, "hit") == 1
        assert _count(telemetry, "dropped") == 0

    async def test_a_failed_fetch_ahead_is_a_miss(self) -> None:
        cdn = _Cdn(statuses={"seg-2.ts": [503]})
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            with capture_logs() as logs:
                await _segment(client, read_ahead, _SEGMENTS[0])
                await _settle(read_ahead)
            body, _ = await _segment(client, read_ahead, _SEGMENTS[1])
            await _settle(read_ahead)

        assert body == _segment_body(_SEGMENTS[1])
        assert _count(telemetry, "failed") == 1
        assert _count(telemetry, "miss") == 2
        failed = next(e for e in logs if e["event"] == "hls_readahead_failed")
        assert failed["cdn"] == "cdn" and failed["error"] == "HTTPStatusError"

    async def test_aclose_cancels_the_fetches_in_flight(self) -> None:
        cdn = _Cdn()
        cdn.block = asyncio.Event()
        telemetry = Telemetry()
        read_ahead = SegmentReadAhead(telemetry=telemetry)
        read_ahead.remember(_PLAYLIST, _SEGMENTS)
        async with cdn.client() as client:
            cdn.block.set()
            await _segment(client, read_ahead, _SEGMENTS[0])
            cdn.block.clear()
            tasks = list(read_ahead._playbacks[_PLAYLIST].ahead.values())
            await asyncio.sleep(0.01)
            await read_ahead.aclose()
            await asyncio.gather(*tasks, return_exceptions=True)

        assert all(task.cancelled() for task in tasks)
        assert read_ahead._playbacks == {} and read_ahead._playlist_of == {}
        assert _count(telemetry, "dropped") == 2


class TestListedSegments:
    _PROXY = "http://scavengarr.test/api/v1/stremio/proxy/hls-abc.1234/"

    def test_the_cdn_urls_the_proxy_fetches_for_the_listed_lines(self) -> None:
        rewritten = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:4\n"
            '#EXT-X-MAP:URI="' + self._PROXY + 'init.mp4?t=abc"\n'
            "#EXTINF:4.0,\n"
            f"{self._PROXY}720p/seg-1.ts?t=abc\n"
            "#EXTINF:4.0,\n"
            f"{self._PROXY}720p/seg-2.ts\n"
            "#EXTINF:4.0,\n"
            f"{self._PROXY}/secure/98/seg-3.ts?t=xyz\n"
            "#EXTINF:4.0,\n"
            "https://other.test/ad.ts\n"
            "\n"
            "#EXT-X-ENDLIST\n"
        )

        urls = listed_segments(
            rewritten,
            self._PROXY,
            "https://cdn.test/v/",
            "https://cdn.test/v/master.m3u8?t=link",
        )

        assert urls == [
            "https://cdn.test/v/720p/seg-1.ts?t=abc",
            "https://cdn.test/v/720p/seg-2.ts?t=link",
            "https://cdn.test/secure/98/seg-3.ts?t=xyz",
        ]

    def test_a_master_playlist_lists_no_segments(self) -> None:
        rewritten = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1280000\n"
            f"{self._PROXY}720p.m3u8?t=abc\n"
        )

        assert listed_segments(rewritten, self._PROXY, "https://cdn.test/v/", "") == []


@pytest.mark.parametrize("size", [0, 1])
async def test_remember_without_segments_changes_nothing(size: int) -> None:
    read_ahead = SegmentReadAhead()
    read_ahead.remember(_PLAYLIST, _SEGMENTS[:size])

    assert (_PLAYLIST in read_ahead._playbacks) == (size == 1)
