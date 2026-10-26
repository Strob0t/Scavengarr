"""Tests for the playback check applied to resolved stream URLs."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers import _verify
from scavengarr.infrastructure.hoster_resolvers._verify import (
    _SNIFF_BYTES,
    PlaybackCheck,
    check_playable,
)
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

from .test_mp4_header import _FTYP, _mdat_header, _moov, _trak

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
        """A timeout of the first-KiB check on a loaded production host cached a working
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
    async def test_a_playlists_range_answer_is_no_file_size(self) -> None:
        # A CDN answering the playlist with 206 sizes the playlist (53
        # bytes), which Stremio showed as "1 MB"
        playlist = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n"
        respx.get(_M3U8).respond(
            206,
            content=playlist,
            headers={"Content-Range": f"bytes 0-{len(playlist) - 1}/{len(playlist)}"},
        )

        check = await _measure(ResolvedStream(_M3U8, is_hls=True))

        assert check == PlaybackCheck(playable=True)

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


class TestMp4Measurement:
    """An MP4 file's video size from its header boxes: from the checked
    bytes when its ``moov`` comes first, else from one more 4 KiB read
    where it starts, within the check's timeout."""

    _MDAT = 10**9
    _MOOV_AT = len(_FTYP) + _MDAT
    _TOTAL = _MOOV_AT + 5000
    _TAIL_RANGE = f"bytes={_MOOV_AT}-{_MOOV_AT + _SNIFF_BYTES - 1}"
    _TAIL_ANSWER = f"bytes {_MOOV_AT}-{_TOTAL - 1}/{_TOTAL}"

    def _head_route(self, content: bytes) -> respx.Route:
        return respx.get(
            _MP4, headers={"Range": f"bytes=0-{_SNIFF_BYTES - 1}"}
        ).respond(
            206,
            content=content,
            headers={"Content-Range": f"bytes 0-{_SNIFF_BYTES - 1}/{self._TOTAL}"},
        )

    def _tail_route(self) -> respx.Route:
        return respx.get(_MP4, headers={"Range": self._TAIL_RANGE})

    @respx.mock
    async def test_fast_start_file_gives_its_size_without_another_read(self) -> None:
        head = self._head_route(
            _FTYP + _moov(_trak(1920, 800)) + _mdat_header(self._MDAT)
        )
        tail = self._tail_route().respond(206)

        check = await _measure(ResolvedStream(_MP4))

        assert check == PlaybackCheck(True, 1920, 800, self._TOTAL)
        assert head.call_count == 1
        assert not tail.called

    @respx.mock
    async def test_moov_after_the_media_data_is_read_where_it_starts(self) -> None:
        self._head_route(_FTYP + _mdat_header(self._MDAT) + bytes(64))
        tail = self._tail_route().respond(
            206,
            content=_moov(_trak(0, 0), _trak(1280, 720)),
            headers={"Content-Range": self._TAIL_ANSWER},
        )

        check = await _measure(ResolvedStream(_MP4, headers={"Referer": "https://h/"}))

        assert check == PlaybackCheck(True, 1280, 720, self._TOTAL)
        assert tail.call_count == 1
        sent = tail.calls.last.request
        assert sent.headers["Referer"] == "https://h/"
        assert sent.headers["User-Agent"] == DEFAULT_USER_AGENT

    @respx.mock
    async def test_a_failed_moov_read_measures_nothing(self) -> None:
        self._head_route(_FTYP + _mdat_header(self._MDAT))
        self._tail_route().mock(side_effect=httpx.ReadTimeout("slow"))

        with structlog.testing.capture_logs() as logs:
            check = await _measure(ResolvedStream(_MP4))

        # Playable: the file answered, only its header was not read
        assert check == PlaybackCheck(True, size_bytes=self._TOTAL)
        unread = [e for e in logs if e["event"] == "playback_check_moov_unread"]
        assert unread == [
            {
                "event": "playback_check_moov_unread",
                "log_level": "info",
                "cdn": "example",
                "error": "ReadTimeout",
            }
        ]

    @respx.mock
    @pytest.mark.parametrize(
        ("status", "content_range"),
        [
            # A CDN that ignores the range sends the file from its start
            (200, None),
            (206, "bytes 0-4095/1000005000"),
            (416, None),
        ],
    )
    async def test_an_answer_from_elsewhere_measures_nothing(
        self, status: int, content_range: str | None
    ) -> None:
        self._head_route(_FTYP + _mdat_header(self._MDAT))
        headers = {"Content-Range": content_range} if content_range else {}
        self._tail_route().respond(
            status, content=_moov(_trak(1920, 1080)), headers=headers
        )

        check = await _measure(ResolvedStream(_MP4))

        assert (check.playable, check.width, check.height) == (True, None, None)

    @respx.mock
    async def test_the_moov_read_ends_with_the_checks_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_verify, "_PLAYBACK_CHECK_TIMEOUT_S", 0.3)

        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(206)

        self._head_route(_FTYP + _mdat_header(self._MDAT))
        self._tail_route().mock(side_effect=slow)

        started = time.monotonic()
        check = await _measure(ResolvedStream(_MP4))

        assert time.monotonic() - started < 2
        assert (check.playable, check.width) == (True, None)

    @respx.mock
    async def test_no_moov_read_without_time_left(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_verify, "_PLAYBACK_CHECK_TIMEOUT_S", 0.0)
        self._head_route(_FTYP + _mdat_header(self._MDAT))
        tail = self._tail_route().respond(206)

        check = await _measure(ResolvedStream(_MP4))

        assert check.playable
        assert not tail.called

    @respx.mock
    async def test_no_moov_read_without_the_files_total(self) -> None:
        respx.get(_MP4, headers={"Range": f"bytes=0-{_SNIFF_BYTES - 1}"}).respond(
            206, content=_FTYP + _mdat_header(self._MDAT)
        )
        tail = self._tail_route().respond(206)

        await _measure(ResolvedStream(_MP4))

        assert not tail.called

    @respx.mock
    async def test_a_playlist_is_not_read_as_mp4(self) -> None:
        route = respx.get(_M3U8).respond(
            206,
            content=b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n",
            headers={"Content-Range": "bytes 0-31/32"},
        )

        await _measure(ResolvedStream(_M3U8, is_hls=True))

        assert route.call_count == 1

    @respx.mock
    async def test_the_result_carries_the_files_quality(self) -> None:
        # Like an HLS master playlist's: the registry's measured quality
        self._head_route(_FTYP + _moov(_trak(1920, 1080)))
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_StubResolver()], http_client=client, verify_playback=True
            )
            result = await registry.resolve("https://voe.sx/e/1")

        assert result is not None
        assert result.quality is StreamQuality.HD_1080P
