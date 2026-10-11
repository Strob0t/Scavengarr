"""Tests for scripts/stremio_playcheck.py (no live requests)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))  # the script imports stremio_measure
    try:
        spec = importlib.util.spec_from_file_location(
            "stremio_playcheck", _SCRIPTS / "stremio_playcheck.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()


class TestMediaKind:
    def test_mpeg_ts(self) -> None:
        packet = b"\x47" + b"\x00" * 187
        assert _mod.media_kind(packet * 2) == "mpegts"

    def test_mp4(self) -> None:
        assert _mod.media_kind(b"\x00\x00\x00\x20ftypisom") == "mp4"

    @pytest.mark.parametrize(
        ("data", "kind"),
        [
            (b"<html><body>403</body></html>", "html"),
            (b"error_wrong_ip", "bytes:6572726f725f7772"),
        ],
    )
    def test_not_media(self, data: bytes, kind: str) -> None:
        """DoodStream's CDN answers a foreign IP with 200 "error_wrong_ip"."""
        assert _mod.media_kind(data) == kind
        assert not _mod.is_media(kind)


class TestCheckStream:
    @respx.mock
    async def test_hls_variant_from_root(self) -> None:
        """The case that found the proxy bug: the master is fine, the
        variant is not."""
        respx.get("https://cdn.example/hls/master.m3u8").respond(
            200, text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n/secure/v.m3u8\n"
        )
        respx.get("https://cdn.example/secure/v.m3u8").respond(404)
        stream = {"url": "https://cdn.example/hls/master.m3u8"}

        async with httpx.AsyncClient() as client:
            verdict = await _mod.check_stream(client, stream)

        assert verdict == "FAIL variant 404"

    @respx.mock
    async def test_file_with_seek_and_headers(self) -> None:
        route = respx.get("https://cdn.example/v.mp4")
        route.side_effect = [
            httpx.Response(
                206,
                content=b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100,
                headers={"content-range": "bytes 0-2047/3000000000"},
            ),
            httpx.Response(206, content=b"\x00" * 1024),
        ]
        stream = {
            "url": "https://cdn.example/v.mp4",
            "behaviorHints": {"proxyHeaders": {"request": {"Referer": "https://h/"}}},
        }

        async with httpx.AsyncClient() as client:
            verdict = await _mod.check_stream(client, stream)

        assert verdict == "OK mp4 3.00 GB, seek 206"
        assert route.calls[0].request.headers["Referer"] == "https://h/"
        assert route.calls[1].request.headers["Range"] == "bytes=1500000000-1500001023"


class TestStreamKind:
    """The eleventh round failed 21 proxied files as ``FAIL master 200``:
    every ``/stremio/proxy/`` URL went to the HLS check."""

    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("https://scavengarr.lan/api/v1/stremio/proxy/ab12/file", "file"),
            ("https://scavengarr.lan/api/v1/stremio/proxy/ab12/file?x=1", "file"),
            ("https://scavengarr.lan/api/v1/stremio/proxy/ab12/scavengarr.m3u8", "hls"),
            ("https://scavengarr.lan/api/v1/stremio/proxy/ab12/", "hls"),
            ("https://cdn.example/hls/master.txt", "hls"),
            ("https://cdn.example/v.m3u8?t=1", "hls"),
            ("https://cdn.example/v.mp4", "file"),
        ],
    )
    def test_kind_by_path(self, url: str, kind: str) -> None:
        assert _mod.stream_kind(url) == kind

    @respx.mock
    async def test_a_proxied_file_is_checked_as_a_file(self) -> None:
        url = "https://scavengarr.lan/api/v1/stremio/proxy/ab12/file"
        respx.get(url).mock(
            side_effect=[
                httpx.Response(
                    206,
                    content=_MP4_CHUNK,
                    headers={"content-range": "bytes 0-2047/3000000000"},
                ),
                httpx.Response(206, content=b"\x00" * 1024),
            ]
        )
        async with httpx.AsyncClient() as client:
            verdict = await _mod.check_stream(client, {"url": url})

        assert verdict == "OK mp4 3.00 GB, seek 206"


class _Body(httpx.AsyncByteStream):
    """A streamed body that never ends: *chunk* again and again, each after
    *delay* seconds. Counts the bytes handed out and whether it was closed."""

    def __init__(self, chunk: bytes, *, delay: float = 0.0) -> None:
        self.chunk = chunk
        self.delay = delay
        self.sent = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            await asyncio.sleep(self.delay)  # 0: let the event loop cancel us
            self.sent += len(self.chunk)
            yield self.chunk

    async def aclose(self) -> None:
        self.closed = True


_TS_PACKET = b"\x47" + b"\x00" * 187
_MP4_CHUNK = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 1012


class TestBoundedReads:
    """The tenth round hung on a proxied file whose Range request got 200 and
    the whole file: the check reads the bytes it judges and closes."""

    async def test_a_full_body_to_a_range_request_costs_a_few_kib(self) -> None:
        head = _Body(_MP4_CHUNK)
        seek = _Body(b"\x00" * 1024)
        with respx.mock:
            respx.get("https://cdn.example/v.mp4").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        headers={"content-length": "3000000000"},
                        stream=head,
                    ),
                    httpx.Response(206, stream=seek),
                ]
            )
            async with httpx.AsyncClient() as client:
                verdict = await asyncio.wait_for(
                    _mod.check_stream(client, {"url": "https://cdn.example/v.mp4"}),
                    timeout=5,
                )

        assert verdict == "OK mp4 3.00 GB, head 200 instead of 206, seek 206"
        assert head.sent <= 64 * 1024 and head.closed
        assert seek.sent <= 64 * 1024 and seek.closed

    async def test_a_seek_answered_with_200_fails(self) -> None:
        with respx.mock:
            respx.get("https://cdn.example/v.mp4").mock(
                side_effect=[
                    httpx.Response(
                        206,
                        content=_MP4_CHUNK,
                        headers={"content-range": "bytes 0-2047/3000000000"},
                    ),
                    httpx.Response(200, stream=_Body(b"\x00" * 1024)),
                ]
            )
            async with httpx.AsyncClient() as client:
                verdict = await asyncio.wait_for(
                    _mod.check_stream(client, {"url": "https://cdn.example/v.mp4"}),
                    timeout=5,
                )

        assert verdict == "FAIL mp4 3.00 GB, seek 200 instead of 206"

    async def test_byte_range_segments_answered_with_200_are_noted(self) -> None:
        """A playlist with ``EXT-X-BYTERANGE`` makes the player ask for
        ranges: a 200 is a finding there."""
        segments = [_Body(_TS_PACKET), _Body(_TS_PACKET)]
        with respx.mock:
            respx.get("https://cdn.example/v.m3u8").respond(
                200,
                text=(
                    "#EXTM3U\n#EXTINF:4,\n#EXT-X-BYTERANGE:1000@0\ns1.ts\n"
                    "#EXTINF:4,\n#EXT-X-BYTERANGE:1000@1000\ns2.ts\n"
                    "#EXTINF:4,\n#EXT-X-BYTERANGE:1000@2000\ns3.ts\n"
                ),
            )
            respx.get("https://cdn.example/s1.ts").mock(
                return_value=httpx.Response(200, stream=segments[0])
            )
            respx.get("https://cdn.example/s2.ts").mock(
                return_value=httpx.Response(200, stream=segments[1])
            )
            async with httpx.AsyncClient() as client:
                verdict = await asyncio.wait_for(
                    _mod.check_stream(client, {"url": "https://cdn.example/v.m3u8"}),
                    timeout=5,
                )

        assert verdict == (
            "OK hls 3 segments: mpegts, mpegts; 2 of 2 segments 200 instead of 206"
        )
        assert all(s.sent <= 64 * 1024 and s.closed for s in segments)

    async def test_whole_segments_answered_with_200_are_normal(self) -> None:
        """Without ``EXT-X-BYTERANGE`` a player asks for whole segments: a
        200 there is no finding."""
        segments = [_Body(_TS_PACKET), _Body(_TS_PACKET)]
        with respx.mock:
            respx.get("https://cdn.example/v.m3u8").respond(
                200, text="#EXTM3U\n#EXTINF:4,\ns1.ts\n#EXTINF:4,\ns2.ts\n"
            )
            respx.get("https://cdn.example/s1.ts").mock(
                return_value=httpx.Response(200, stream=segments[0])
            )
            respx.get("https://cdn.example/s2.ts").mock(
                return_value=httpx.Response(200, stream=segments[1])
            )
            async with httpx.AsyncClient() as client:
                verdict = await asyncio.wait_for(
                    _mod.check_stream(client, {"url": "https://cdn.example/v.m3u8"}),
                    timeout=5,
                )

        assert verdict == "OK hls 2 segments: mpegts, mpegts"
        assert all(s.sent <= 64 * 1024 and s.closed for s in segments)

    async def test_an_endless_playlist_answer_is_cut(self) -> None:
        body = _Body(b"x" * 4096)
        with respx.mock:
            respx.get("https://cdn.example/v.m3u8").mock(
                return_value=httpx.Response(200, stream=body)
            )
            async with httpx.AsyncClient() as client:
                verdict = await asyncio.wait_for(
                    _mod.check_stream(client, {"url": "https://cdn.example/v.m3u8"}),
                    timeout=5,
                )

        assert verdict == "FAIL master 200"
        assert body.sent <= 2 * 1024 * 1024 and body.closed

    async def test_the_check_ends_at_its_deadline_on_a_slow_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_mod, "_CHECK_TIMEOUT", 0.3)
        body = _Body(_MP4_CHUNK, delay=10.0)
        with respx.mock:
            respx.get("https://cdn.example/v.mp4").mock(
                return_value=httpx.Response(
                    206, headers={"content-range": "bytes 0-2047/3000"}, stream=body
                )
            )
            async with httpx.AsyncClient() as client:
                started = time.monotonic()
                verdict = await asyncio.wait_for(
                    _mod.check_stream(client, {"url": "https://cdn.example/v.mp4"}),
                    timeout=5,
                )

        assert verdict == "FAIL timeout after 0.3 s"
        assert time.monotonic() - started < 2.0
        assert body.closed
