"""Tests for the HLS proxy helpers (manifest rewriting + CDN fetch)."""

from __future__ import annotations

import asyncio
import contextlib
import gc
import gzip
import socket
import struct
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
import structlog

from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.stremio import hls_proxy
from scavengarr.infrastructure.stremio.hls_proxy import (
    AheadSegment,
    FileCut,
    FileResume,
    ProxyWatch,
    SegmentCut,
    build_cdn_url,
    cdn_base_from_url,
    fetch_hls_resource,
    resolve_query_string,
    rewrite_manifest,
    stream_file,
    stream_hls_segment,
)
from scavengarr.infrastructure.telemetry.metrics import Telemetry


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> None:
    """Clear the module-level manifest cache and the first answers logged
    between tests."""
    hls_proxy._manifest_cache.clear()
    hls_proxy._first_answers.clear()


# ---------------------------------------------------------------------------
# cdn_base_from_url
# ---------------------------------------------------------------------------


class TestCdnBaseFromUrl:
    def test_extracts_directory_from_hls_url(self) -> None:
        url = "https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/master.m3u8?t=abc"
        assert (
            cdn_base_from_url(url)
            == "https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/"
        )

    def test_root_path(self) -> None:
        url = "https://cdn.example.com/master.m3u8"
        assert cdn_base_from_url(url) == "https://cdn.example.com/"

    def test_nested_path(self) -> None:
        url = "https://cdn.example.com/a/b/c/video.m3u8"
        assert cdn_base_from_url(url) == "https://cdn.example.com/a/b/c/"

    def test_no_trailing_file(self) -> None:
        url = "https://cdn.example.com/path/"
        assert cdn_base_from_url(url) == "https://cdn.example.com/path/"

    def test_preserves_scheme(self) -> None:
        url = "http://cdn.example.com/video/master.m3u8"
        assert cdn_base_from_url(url) == "http://cdn.example.com/video/"


# ---------------------------------------------------------------------------
# rewrite_manifest
# ---------------------------------------------------------------------------


_MASTER_MANIFEST = """\
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=720x480
index-v1-a1.m3u8?t=abc123
#EXT-X-STREAM-INF:BANDWIDTH=2560000,RESOLUTION=1280x720
index-v2-a1.m3u8?t=abc123
"""

_VARIANT_MANIFEST = """\
#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/seg-1-v1-a1.ts?t=abc
#EXTINF:10.0,
https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/seg-2-v1-a1.ts?t=abc
#EXT-X-ENDLIST
"""


class TestRewriteManifest:
    def test_relative_uris_point_at_the_playlists_link(self) -> None:
        """Resolved against the playlist's proxy URL, a relative URI went to
        the shared link of the stream, which a later resolution can move to
        another CDN node (code review, 2026-10-06)."""
        cdn_base = "https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/"
        proxy_base = "http://localhost:7979/api/v1/stremio/proxy/abc123.pinned/"

        result = rewrite_manifest(_MASTER_MANIFEST, cdn_base, proxy_base)

        assert f"\n{proxy_base}index-v1-a1.m3u8?t=abc123\n" in result
        assert f"\n{proxy_base}index-v2-a1.m3u8?t=abc123\n" in result

    def test_relative_uris_of_a_variant_resolve_in_its_directory(self) -> None:
        cdn_base = "https://cdn.example.com/hls/a/"
        proxy_base = "http://proxy/p/sid.pinned/"
        content = "#EXTM3U\n#EXTINF:4.0,\nseg-1.ts\n"

        result = rewrite_manifest(content, cdn_base, proxy_base, playlist_dir="720p/")

        assert result.splitlines()[2] == "http://proxy/p/sid.pinned/720p/seg-1.ts"

    @pytest.mark.parametrize("uri", ["data:text/plain;base64,AAAA", "skd://key-id"])
    def test_uris_of_other_schemes_stay(self, uri: str) -> None:
        content = f'#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="{uri}"\n'

        result = rewrite_manifest(content, "https://cdn.example.com/a/", "http://p/s/")

        assert f'URI="{uri}"' in result

    def test_variant_manifest_absolute_urls_rewritten(self) -> None:
        cdn_base = "https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/"
        proxy_base = "http://localhost:7979/api/v1/stremio/proxy/abc123/"
        result = rewrite_manifest(_VARIANT_MANIFEST, cdn_base, proxy_base)
        assert cdn_base not in result
        assert (
            "http://localhost:7979/api/v1/stremio/proxy/abc123/seg-1-v1-a1.ts?t=abc"
            in result
        )
        assert (
            "http://localhost:7979/api/v1/stremio/proxy/abc123/seg-2-v1-a1.ts?t=abc"
            in result
        )

    def test_preserves_tags_and_comments(self) -> None:
        cdn_base = "https://cdn.example.com/"
        proxy_base = "http://proxy/"
        result = rewrite_manifest(_VARIANT_MANIFEST, cdn_base, proxy_base)
        assert "#EXTM3U" in result
        assert "#EXT-X-TARGETDURATION:10" in result
        assert "#EXT-X-ENDLIST" in result

    def test_empty_manifest(self) -> None:
        result = rewrite_manifest("", "https://cdn.example.com/", "http://proxy/")
        assert result == ""

    def test_manifest_with_no_matching_urls(self) -> None:
        content = "#EXTM3U\n#EXT-X-ENDLIST\n"
        result = rewrite_manifest(content, "https://cdn.example.com/", "http://proxy/")
        assert result == content

    def test_host_in_other_case_goes_through_the_proxy(self) -> None:
        """VidHide's stream URL names its CDN host in mixed case and its
        playlists list the segments under the lower-case host: compared
        as written, every segment stayed unproxied and the player got 403
        for a token bound to the production host (step 27, 2026-10-07)."""
        cdn_base = "https://2ZO6sb3MYz7fapc.acek-cdn.com/hls2/01/abc_h/"
        proxy_base = "https://scavengarr.example.lan/api/v1/stremio/proxy/sid/"
        content = (
            "#EXTM3U\n#EXTINF:6.0,\n"
            "https://2zo6sb3myz7fapc.acek-cdn.com/hls2/01/abc_h/seg-1-v1-a1.ts?t=x\n"
        )

        result = rewrite_manifest(content, cdn_base, proxy_base, "index/")

        assert result.splitlines()[-1] == (
            proxy_base + "/hls2/01/abc_h/seg-1-v1-a1.ts?t=x"
        )

    def test_root_relative_uri_goes_through_the_proxy(self) -> None:
        """Vidsonic's master lists its variant from the CDN root
        (``/secure/98/<id>/video.m3u8``): left as is, the player resolves
        it against the proxy's host and gets a 404."""
        cdn_base = "https://ve12.vidsonic.net/hls/r00nm90ihj96/"
        proxy_base = "https://scavengarr.example.lan/api/v1/stremio/proxy/sid/"
        content = (
            "#EXTM3U\n"
            '#EXT-X-STREAM-INF:BANDWIDTH=1646000,CODECS="avc1.64001f,mp4a.40.2"\n'
            "/secure/98/r00nm90ihj96/video.m3u8?expires=1&md5=ab&server_id=3\n"
        )

        result = rewrite_manifest(content, cdn_base, proxy_base)

        assert result.splitlines()[-1] == (
            proxy_base
            + "/secure/98/r00nm90ihj96/video.m3u8?expires=1&md5=ab&server_id=3"
        )
        # The proxy maps that path back to the CDN root
        path = result.splitlines()[-1].removeprefix(proxy_base).split("?")[0]
        assert build_cdn_url(cdn_base, path) == (
            "https://ve12.vidsonic.net/secure/98/r00nm90ihj96/video.m3u8"
        )

    def test_same_host_url_outside_the_base_goes_through_the_proxy(self) -> None:
        cdn_base = "https://cdn.example.com/hls/a/"
        proxy_base = "http://proxy/p/sid/"
        content = "#EXTM3U\n#EXTINF:10,\nhttps://cdn.example.com/seg/b/1.ts?t=x\n"

        result = rewrite_manifest(content, cdn_base, proxy_base)

        assert result.splitlines()[-1] == "http://proxy/p/sid//seg/b/1.ts?t=x"

    def test_protocol_relative_uri_of_the_cdn(self) -> None:
        cdn_base = "https://cdn.example.com/hls/a/"
        proxy_base = "http://proxy/p/sid/"
        content = "#EXTM3U\n#EXTINF:10,\n//cdn.example.com/seg/1.ts\n"

        result = rewrite_manifest(content, cdn_base, proxy_base)

        assert result.splitlines()[-1] == "http://proxy/p/sid//seg/1.ts"

    @pytest.mark.parametrize(
        "uri",
        [
            "https://other.example.net/aud/index.m3u8",
            "//other.example.net/aud/index.m3u8",
            "http://cdn.example.com/hls/a/seg.ts",  # other scheme: another origin
        ],
    )
    def test_other_origins_stay_direct(self, uri: str) -> None:
        """The proxy only fetches from the stream's own CDN."""
        content = f"#EXTM3U\n#EXTINF:10,\n{uri}\n"

        result = rewrite_manifest(
            content, "https://cdn.example.com/hls/a/", "http://proxy/p/sid/"
        )

        assert result == content

    def test_uri_attributes_of_tags(self) -> None:
        """Audio renditions, keys and init segments sit in ``URI="…"``."""
        cdn_base = "https://cdn.example.com/hls/a/"
        proxy_base = "http://proxy/p/sid/"
        content = (
            "#EXTM3U\n"
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="German",'
            'URI="/aud/de/index.m3u8"\n'
            '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.example.com/hls/a/key.bin"\n'
            '#EXT-X-MAP:URI="init.mp4"\n'
            '#EXT-X-MEDIA:TYPE=SUBTITLES,URI="https://subs.example.net/de.m3u8"\n'
        )

        lines = rewrite_manifest(content, cdn_base, proxy_base).splitlines()

        assert lines[1].endswith('URI="http://proxy/p/sid//aud/de/index.m3u8"')
        assert lines[2].endswith('URI="http://proxy/p/sid/key.bin"')
        assert lines[3] == '#EXT-X-MAP:URI="http://proxy/p/sid/init.mp4"'
        assert lines[4].endswith('URI="https://subs.example.net/de.m3u8"')

    def test_keeps_line_endings(self) -> None:
        content = "#EXTM3U\r\n#EXTINF:10,\r\n/seg/1.ts\r\n"

        result = rewrite_manifest(
            content, "https://cdn.example.com/hls/", "http://proxy/p/sid/"
        )

        assert result == "#EXTM3U\r\n#EXTINF:10,\r\nhttp://proxy/p/sid//seg/1.ts\r\n"


# ---------------------------------------------------------------------------
# fetch_hls_resource
# ---------------------------------------------------------------------------


class TestManifestCacheBound:
    """Rotating manifest URLs (tokens in the query) must not pile up."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_expired_manifests_are_pruned_when_full(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hls_proxy, "_MANIFEST_CACHE_MAX", 3)
        past = time.monotonic() - 1
        for i in range(3):
            hls_proxy._manifest_cache[f"https://cdn/old{i}.m3u8"] = (b"", "x", past)
        url = "https://cdn.example.com/new.m3u8"
        respx.get(url).respond(200, content=b"#EXTM3U\n")

        async with httpx.AsyncClient() as client:
            await fetch_hls_resource(client, url, {})

        assert list(hls_proxy._manifest_cache) == [url]

    @respx.mock
    @pytest.mark.asyncio()
    async def test_oldest_manifest_dropped_when_full_of_fresh_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hls_proxy, "_MANIFEST_CACHE_MAX", 3)
        future = time.monotonic() + 60
        for i in range(3):
            hls_proxy._manifest_cache[f"https://cdn/fresh{i}.m3u8"] = (
                b"",
                "x",
                future,
            )
        url = "https://cdn.example.com/new.m3u8"
        respx.get(url).respond(200, content=b"#EXTM3U\n")

        async with httpx.AsyncClient() as client:
            await fetch_hls_resource(client, url, {})

        assert list(hls_proxy._manifest_cache) == [
            "https://cdn/fresh1.m3u8",
            "https://cdn/fresh2.m3u8",
            url,
        ]


class TestFetchHlsResource:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_fetches_manifest_with_headers(self) -> None:
        url = "https://cdn.example.com/video/master.m3u8"
        content = b"#EXTM3U\n#EXT-X-ENDLIST\n"
        route = respx.get(url).respond(
            200,
            content=content,
            headers={"Content-Type": "application/vnd.apple.mpegurl"},
        )

        async with httpx.AsyncClient() as client:
            body, ct = await fetch_hls_resource(
                client, url, {"Referer": "https://dropload.io/"}
            )

        assert body == content
        assert "mpegurl" in ct
        assert route.called
        # Verify Referer was sent
        sent_headers = route.calls[0].request.headers
        assert sent_headers["referer"] == "https://dropload.io/"
        # The proxy fetches on the player's behalf: with the player's agent
        assert sent_headers["user-agent"] == DEFAULT_USER_AGENT

    @respx.mock
    @pytest.mark.asyncio()
    async def test_segment_is_streamed_with_the_players_user_agent(self) -> None:
        url = "https://cdn.example.com/video/seg-2.ts"
        route = respx.get(url).respond(200, content=b"\x47\x40")

        async with httpx.AsyncClient() as client:
            answer = await stream_hls_segment(client, url, {})
            chunks = answer.chunks
            async for _chunk in chunks:
                pass

        assert route.calls[0].request.headers["user-agent"] == DEFAULT_USER_AGENT

    @respx.mock
    @pytest.mark.asyncio()
    async def test_fetches_segment(self) -> None:
        url = "https://cdn.example.com/video/seg-1.ts"
        content = b"\x00\x01\x02segment-data"
        respx.get(url).respond(
            200,
            content=content,
            headers={"Content-Type": "video/mp2t"},
        )

        async with httpx.AsyncClient() as client:
            body, ct = await fetch_hls_resource(client, url, {})

        assert body == content
        assert ct == "video/mp2t"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_raises_on_non_2xx(self) -> None:
        url = "https://cdn.example.com/video/master.m3u8"
        respx.get(url).respond(403)

        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_hls_resource(
                    client, url, {"Referer": "https://dropload.io/"}
                )

    @respx.mock
    @pytest.mark.asyncio()
    async def test_raises_on_network_error(self) -> None:
        url = "https://cdn.example.com/video/master.m3u8"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await fetch_hls_resource(client, url, {})


# ---------------------------------------------------------------------------
# build_cdn_url
# ---------------------------------------------------------------------------


class TestBuildCdnUrl:
    def test_relative_path(self) -> None:
        base = "https://cdn.example.com/hls/video/"
        result = build_cdn_url(base, "seg-1.ts", "t=abc")
        assert result == "https://cdn.example.com/hls/video/seg-1.ts?t=abc"

    def test_no_query_string(self) -> None:
        base = "https://cdn.example.com/hls/video/"
        result = build_cdn_url(base, "seg-1.ts")
        assert result == "https://cdn.example.com/hls/video/seg-1.ts"

    def test_absolute_path(self) -> None:
        base = "https://cdn.example.com/hls/video/"
        result = build_cdn_url(base, "/other/seg-1.ts")
        assert result == "https://cdn.example.com/other/seg-1.ts"

    def test_host_in_other_case_stays_on_the_cdn(self) -> None:
        base = "https://CDN.Example.com/hls/video/"
        result = build_cdn_url(base, "//cdn.example.com/other/seg-1.ts")
        assert result == "https://cdn.example.com/other/seg-1.ts"

    def test_nested_relative_path(self) -> None:
        base = "https://cdn.example.com/hls/"
        result = build_cdn_url(base, "video/seg-1.ts", "t=abc")
        assert result == "https://cdn.example.com/hls/video/seg-1.ts?t=abc"

    @pytest.mark.parametrize(
        "path",
        [
            "http://127.0.0.1:8080/admin",
            "https://other.example.net/seg.ts",
            "//169.254.169.254/latest/meta-data",
            "http://cdn.example.com/hls/seg.ts",  # scheme downgrade
        ],
    )
    def test_rejects_other_hosts(self, path: str) -> None:
        """The client-supplied path must not leave the stream's CDN (SSRF)."""
        with pytest.raises(ValueError, match="CDN"):
            build_cdn_url("https://cdn.example.com/hls/", path)


class TestStreamHlsSegment:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_error_response_is_closed(self) -> None:
        """A failed segment must not keep its pooled connection."""
        url = "https://cdn.example.com/video/seg-1.ts"
        respx.get(url).respond(403)
        seen: list[httpx.Response] = []

        async def _keep(resp: httpx.Response) -> None:
            seen.append(resp)

        async with httpx.AsyncClient(event_hooks={"response": [_keep]}) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await hls_proxy.stream_hls_segment(client, url, {})

        assert seen and seen[0].is_closed

    async def test_small_cdn_chunks_go_out_in_64_kib_pieces(self) -> None:
        """VOE's CDN sends 4 KiB TLS records. Passed through one by one, each
        a response write, the proxy used 39-46 ms of CPU per MB against
        34-36 ms in 64 KiB pieces (dev-server end-to-end run, 2026-10-05)."""
        parts = [bytes([i]) * 4096 for i in range(40)]

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_Chunks(parts))

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
            chunks = answer.chunks
            received = [chunk async for chunk in chunks]

        assert [len(chunk) for chunk in received] == [65536, 65536, 32768]
        assert b"".join(received) == b"".join(parts)

    async def test_a_head_request_reads_no_segment_bytes(self) -> None:
        """HEAD wants the CDN's status and content type, not its 2-10 MB."""
        body = _Chunks([b"\x47" * 1000])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"Content-Type": "video/mp2t"}, stream=body
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, head=True
            )
            chunks, content_type = (
                answer.chunks,
                answer.headers.get("content-type", "application/octet-stream"),
            )
            received = [chunk async for chunk in chunks]

        assert received == []
        assert content_type == "video/mp2t"
        assert not body.read
        assert body.closed

    async def test_an_encoded_segment_is_decoded(self) -> None:
        """The proxy does not forward Content-Encoding: it sends plain bytes."""
        data = b"\x47segment" * 100

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                content=gzip.compress(data),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
            chunks = answer.chunks
            received = b"".join([chunk async for chunk in chunks])

        assert received == data
        assert "content-encoding" not in answer.headers

    async def test_a_segment_range_reaches_the_cdn_and_comes_back(self) -> None:
        """The desktop Stremio app's player asks every HLS segment with
        ``Range: bytes=0-``. Passed through, the player learns the segment's
        length; without it, a chunked body of unknown size had it re-request
        segment after segment (2026-11-13)."""
        seen: list[tuple[str | None, str | None, str | None]] = []

        def _cdn(request: httpx.Request) -> httpx.Response:
            seen.append(
                (
                    request.headers.get("range"),
                    request.headers.get("if-range"),
                    request.headers.get("accept-encoding"),
                )
            )
            return httpx.Response(
                206,
                headers={
                    "Content-Type": "video/mp2t",
                    "Content-Length": "4",
                    "Content-Range": "bytes 0-3/4",
                    "Accept-Ranges": "bytes",
                },
                content=b"\x47\x40\x00\x10",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(
                client,
                "https://cdn.test/1.ts",
                {},
                player={"Range": "bytes=0-", "If-Range": '"etag-1"'},
            )
            received = b"".join([chunk async for chunk in answer.chunks])

        assert seen == [("bytes=0-", '"etag-1"', "identity")]
        assert answer.status == 206
        assert answer.headers["content-range"] == "bytes 0-3/4"
        assert answer.headers["content-length"] == "4"
        assert received == b"\x47\x40\x00\x10"

    async def test_an_unsatisfiable_segment_range_passes_through(self) -> None:
        """A 416 is the player's answer to see, not a 502: the file proxy
        behaves the same way, and the streamed answer is closed first."""
        body = _Chunks([b""])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                416,
                headers={"Content-Range": "bytes */4"},
                stream=body,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, player={"Range": "bytes=9-"}
            )
            received = [chunk async for chunk in answer.chunks]

        assert answer.status == 416
        assert answer.headers["content-range"] == "bytes */4"
        assert received == []
        assert body.closed

    async def test_a_segment_keeps_the_length_the_cdn_framed(self) -> None:
        """A framed body keeps its Content-Length (not chunked as before)."""

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "video/mp2t", "Content-Length": "3"},
                content=b"\x47\x40\x00",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
            [chunk async for chunk in answer.chunks]

        assert answer.status == 200
        assert answer.headers["content-length"] == "3"

    async def test_a_chunked_segment_declares_no_length(self) -> None:
        """A body the CDN chunks promises no length; the answer must not invent
        one (uvicorn refuses a body of another length)."""

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "video/mp2t"},
                stream=_Chunks([b"\x47" * 1000]),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
            [chunk async for chunk in answer.chunks]

        assert answer.status == 200
        assert "content-length" not in answer.headers

    async def test_an_encoded_segment_declares_no_length_or_span(self) -> None:
        """The body goes out decoded, so the encoded entity's length and span
        must not frame it (uvicorn refuses a body of another length), and a
        206 whose span was dropped answers as the whole segment."""
        data = b"\x47segment" * 100
        encoded = gzip.compress(data)

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                206,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Type": "video/mp2t",
                    "Content-Length": str(len(encoded)),
                    "Content-Range": f"bytes 0-{len(encoded) - 1}/{len(encoded)}",
                },
                content=encoded,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
            received = b"".join([chunk async for chunk in answer.chunks])

        assert received == data
        assert answer.status == 200
        assert set(answer.headers) == {"content-type"}

    async def test_the_bytes_sent_are_reported_when_the_body_ends(self) -> None:
        sent: list[int] = []

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_Chunks([b"\x47" * 70000]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, on_sent=sent.append
            )
            chunks = answer.chunks
            [chunk async for chunk in chunks]

        assert sent == [70000]

    async def test_a_body_closed_early_reports_the_bytes_so_far(self) -> None:
        sent: list[int] = []
        body = _Chunks([b"\x47" * 65536, b"\x47" * 1000])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, on_sent=sent.append
            )
            chunks = answer.chunks
            first = await chunks.__anext__()
            await chunks.aclose()

        assert len(first) == 65536
        assert sent == [65536]
        assert body.closed

    async def test_a_body_dropped_mid_transfer_is_closed_once_by_the_loop(
        self,
    ) -> None:
        """A player's disconnect leaves the body suspended at its ``yield``;
        the event loop's finalizer closes it once it is garbage-collected.
        With a counting wrapper around it, the collection finalized both
        and the two closes raced ("aclose(): asynchronous generator is
        already running", 16 times in 48 h of production, 2026-10-07):
        one generator, one close, the bytes reported once, no task left
        with an exception."""
        sent: list[int] = []
        # Two pieces: the body is suspended with the CDN's answer still open
        body = _Chunks([b"\x47" * 65536, b"\x47" * 65536])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=body)

        loop = asyncio.get_running_loop()
        complaints: list[str] = []
        loop.set_exception_handler(
            lambda _loop, context: complaints.append(context.get("message", ""))
        )
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:

                async def _play() -> None:
                    answer = await stream_hls_segment(
                        client, "https://cdn.test/1.ts", {}, on_sent=sent.append
                    )
                    chunks = answer.chunks
                    await chunks.__anext__()
                    try:
                        raise RuntimeError("the player went away")
                    except RuntimeError as exc:
                        # The frame keeps its own traceback: the suspended
                        # body goes down with the cycle, in a collection
                        kept = exc  # noqa: F841

                await _play()
                gc.collect()
                for _ in range(3):
                    await asyncio.sleep(0)
                others = [
                    t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                ]
                results = await asyncio.gather(*others, return_exceptions=True)
                gc.collect()
        finally:
            loop.set_exception_handler(None)

        assert body.closed
        assert sent == [65536]
        assert [r for r in results if isinstance(r, BaseException)] == []
        assert complaints == []


def _events(logs: list[dict[str, object]], event: str) -> list[dict[str, object]]:
    return [entry for entry in logs if entry["event"] == event]


class TestProxyWatch:
    """The proxy logs a stream's first CDN answer per kind and a player's
    abort before the body ends."""

    _TS = b"\x47\x40\x00\x10\x00\x00\xb0\x0d" + b"\x47" * 70000

    @staticmethod
    def _segment_cdn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "video/mp2t"},
            stream=_Chunks([TestProxyWatch._TS]),
        )

    async def test_the_first_segment_logs_its_answer_once(self) -> None:
        transport = httpx.MockTransport(self._segment_cdn)
        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=transport) as client:
                for number in (1, 2):
                    answer = await stream_hls_segment(
                        client,
                        f"https://cdn.test/hls/{number}.ts?t=secret",
                        {},
                        watch=ProxyWatch("abc", "voe", "segment"),
                    )
                    chunks = answer.chunks
                    [chunk async for chunk in chunks]

        first = _events(logs, "hls_proxy_first_answer")
        assert len(first) == 1
        assert first[0]["stream_id"] == "abc"
        assert first[0]["hoster"] == "voe"
        assert first[0]["kind"] == "segment"
        assert first[0]["cdn_host"] == "cdn.test"
        assert first[0]["status"] == 200
        assert first[0]["content_type"] == "video/mp2t"
        assert first[0]["content_length"] is None
        assert first[0]["first_bytes_hex"] == "474000100000b00d"
        assert "secret" not in str(first[0])

    async def test_another_stream_and_kind_log_their_own(self) -> None:
        transport = httpx.MockTransport(self._segment_cdn)
        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=transport) as client:
                for watch in (
                    ProxyWatch("abc", "voe", "segment"),
                    ProxyWatch("def", "voe", "segment"),
                ):
                    answer = await stream_hls_segment(
                        client, "https://cdn.test/1.ts", {}, watch=watch
                    )
                    chunks = answer.chunks
                    [chunk async for chunk in chunks]
            async with respx.mock:
                respx.get("https://cdn.test/index.m3u8").mock(
                    return_value=httpx.Response(
                        200,
                        content=b"#EXTM3U\n",
                        headers={"Content-Type": "application/vnd.apple.mpegurl"},
                    )
                )
                async with httpx.AsyncClient() as client:
                    await fetch_hls_resource(
                        client,
                        "https://cdn.test/index.m3u8",
                        {},
                        watch=ProxyWatch("abc", "voe", "master"),
                    )

        first = _events(logs, "hls_proxy_first_answer")
        assert [(e["stream_id"], e["kind"]) for e in first] == [
            ("abc", "segment"),
            ("def", "segment"),
            ("abc", "master"),
        ]
        assert first[2]["content_length"] == 8
        assert first[2]["first_bytes_hex"] == b"#EXTM3U\n".hex()

    async def test_the_stream_ids_remembered_are_bounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hls_proxy, "_FIRST_ANSWERS_MAX", 2)
        with structlog.testing.capture_logs() as logs:
            for stream_id in ("a", "b", "c", "a"):
                ProxyWatch(stream_id, "voe", "segment").answered(
                    "https://cdn.test/1.ts", 200, "video/mp2t", None, b""
                )

        assert list(hls_proxy._first_answers) == ["c", "a"]
        assert len(_events(logs, "hls_proxy_first_answer")) == 4

    async def test_a_client_abort_logs_the_bytes_sent(self) -> None:
        body = _Chunks([b"\x47" * 65536, b"\x47" * 1000])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Length": "66536"}, stream=body)

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
                answer = await stream_hls_segment(
                    client,
                    "https://cdn.test/1.ts",
                    {},
                    watch=ProxyWatch("abc", "voe", "segment"),
                )
                chunks = answer.chunks
                await chunks.__anext__()
                await chunks.aclose()

        aborts = _events(logs, "proxy_client_abort")
        assert len(aborts) == 1
        assert aborts[0]["stream_id"] == "abc"
        assert aborts[0]["hoster"] == "voe"
        assert aborts[0]["kind"] == "segment"
        assert aborts[0]["bytes_sent"] == 65536
        assert aborts[0]["expected_bytes"] == 66536
        assert isinstance(aborts[0]["seconds_since_first"], float)
        assert body.closed

    async def test_a_normal_end_logs_no_abort(self) -> None:
        transport = httpx.MockTransport(self._segment_cdn)
        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=transport) as client:
                answer = await stream_hls_segment(
                    client,
                    "https://cdn.test/1.ts",
                    {},
                    watch=ProxyWatch("abc", "voe", "segment"),
                )
                chunks = answer.chunks
                async with contextlib.aclosing(chunks):
                    [chunk async for chunk in chunks]

        assert _events(logs, "proxy_client_abort") == []

    async def test_a_segment_fetched_ahead_logs_its_answer_and_abort(self) -> None:
        read_ahead = MagicMock()
        read_ahead.take = AsyncMock(
            return_value=AheadSegment(body=self._TS, content_type="video/mp2t")
        )
        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient() as client:
                answer = await stream_hls_segment(
                    client,
                    "https://cdn.test/1.ts",
                    {},
                    read_ahead=read_ahead,
                    watch=ProxyWatch("abc", "voe", "segment"),
                )
                chunks = answer.chunks
                await chunks.__anext__()
                await chunks.aclose()

        first = _events(logs, "hls_proxy_first_answer")
        assert len(first) == 1
        assert first[0]["status"] is None
        assert first[0]["content_length"] == len(self._TS)
        assert first[0]["first_bytes_hex"] == "474000100000b00d"
        aborts = _events(logs, "proxy_client_abort")
        assert [(a["bytes_sent"], a["expected_bytes"]) for a in aborts] == [
            (65536, len(self._TS))
        ]

    async def test_the_file_proxy_logs_its_first_answer_once(self) -> None:
        data = b"\x1a\x45\xdf\xa3\x01\x00\x00\x00" + b"\x00" * 1000

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "video/x-matroska", "Content-Length": "1008"},
                stream=_Chunks([data]),
            )

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
                for _ in range(2):
                    answer = await stream_file(
                        client,
                        "https://files.test/v/movie.mkv?token=secret",
                        {},
                        watch=ProxyWatch("abc", "doodstream", "file"),
                    )
                    [chunk async for chunk in answer.chunks]

        first = _events(logs, "file_proxy_first_answer")
        assert len(first) == 1
        assert first[0]["kind"] == "file"
        assert first[0]["cdn_host"] == "files.test"
        assert first[0]["status"] == 200
        assert first[0]["content_type"] == "video/x-matroska"
        assert first[0]["content_length"] == 1008
        assert first[0]["first_bytes_hex"] == "1a45dfa301000000"
        assert "secret" not in str(first[0])
        assert _events(logs, "hls_proxy_first_answer") == []
        assert _events(logs, "proxy_client_abort") == []

    async def test_a_file_abort_logs_the_bytes_sent(self) -> None:
        body = _Chunks([b"\x00" * 65536, b"\x00" * 65536])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"Content-Length": "131072"}, stream=body
            )

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
                answer = await stream_file(
                    client,
                    "https://files.test/movie.mp4",
                    {},
                    watch=ProxyWatch("abc", "doodstream", "file"),
                )
                await answer.chunks.__anext__()
                await answer.chunks.aclose()

        aborts = _events(logs, "proxy_client_abort")
        assert [(a["kind"], a["bytes_sent"], a["expected_bytes"]) for a in aborts] == [
            ("file", 65536, 131072)
        ]


class TestStreamFile:
    """A direct file passes through with the player's byte range."""

    _URL = "https://s-delivery.mxdcontent.example/v/abc.mp4?s=tok"

    async def test_a_whole_file_passes_with_its_headers(self) -> None:
        seen: list[httpx.Request] = []

        def _cdn(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": "70000",
                    "Accept-Ranges": "bytes",
                    "X-Cache": "HIT",
                },
                stream=_Chunks([b"\x00" * 70000]),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_file(
                client, self._URL, {"Referer": "https://mixdrop.ag/"}
            )
            received = [chunk async for chunk in answer.chunks]

        assert answer.status == 200
        assert answer.headers == {
            "content-type": "video/mp4",
            "content-length": "70000",
            "accept-ranges": "bytes",
        }
        assert [len(chunk) for chunk in received] == [65536, 4464]
        request = seen[0]
        assert request.headers["Accept-Encoding"] == "identity"
        assert request.headers["Referer"] == "https://mixdrop.ag/"
        assert request.headers["User-Agent"] == DEFAULT_USER_AGENT
        assert "Range" not in request.headers

    async def test_the_players_range_passes_through(self) -> None:
        seen: list[httpx.Request] = []

        def _cdn(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                206,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": "bytes 1000-1999/5000",
                    "Content-Length": "1000",
                },
                stream=_Chunks([b"\x01" * 1000]),
            )

        player = {
            "Range": "bytes=1000-1999",
            "If-Range": '"etag"',
            "Cookie": "session=1",
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_file(client, self._URL, {}, player=player)
            body = b"".join([chunk async for chunk in answer.chunks])

        assert answer.status == 206
        assert answer.headers["content-range"] == "bytes 1000-1999/5000"
        assert answer.headers["content-length"] == "1000"
        assert len(body) == 1000
        assert seen[0].headers["Range"] == "bytes=1000-1999"
        assert seen[0].headers["If-Range"] == '"etag"'
        assert "Cookie" not in seen[0].headers

    async def test_a_head_request_reads_no_bytes(self) -> None:
        body = _Chunks([b"\x00" * 1000])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "video/mp4", "Content-Length": "1000"},
                stream=body,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_file(client, self._URL, {}, head=True)
            received = [chunk async for chunk in answer.chunks]

        assert received == []
        assert answer.headers["content-length"] == "1000"
        assert not body.read
        assert body.closed

    async def test_an_unsatisfiable_range_passes_through(self) -> None:
        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                416, headers={"Content-Range": "bytes */5000"}, stream=_Chunks([])
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            answer = await stream_file(
                client, self._URL, {}, player={"Range": "bytes=9000-"}
            )
            received = [chunk async for chunk in answer.chunks]

        assert answer.status == 416
        assert answer.headers["content-range"] == "bytes */5000"
        assert received == []

    @respx.mock
    @pytest.mark.asyncio()
    async def test_a_refusal_raises_and_is_closed(self) -> None:
        respx.get(self._URL).respond(403)
        seen: list[httpx.Response] = []

        async def _keep(resp: httpx.Response) -> None:
            seen.append(resp)

        async with httpx.AsyncClient(event_hooks={"response": [_keep]}) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await stream_file(client, self._URL, {})

        assert seen and seen[0].is_closed

    async def test_a_network_error_raises(self) -> None:
        def _cdn(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            with pytest.raises(httpx.ConnectError):
                await stream_file(client, self._URL, {})

    async def test_the_read_timeout_waits_for_the_first_byte(self) -> None:
        """Vinovo's first byte takes about 30 s; the connect timeout stays
        the client's."""
        seen: list[httpx.Request] = []

        def _cdn(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, stream=_Chunks([]))

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(_cdn), timeout=httpx.Timeout(5.0)
        ) as client:
            answer = await stream_file(client, self._URL, {})
            [chunk async for chunk in answer.chunks]

        timeout = seen[0].extensions["timeout"]
        assert timeout["read"] == 60.0
        assert timeout["connect"] == 5.0


class TestFileCut:
    """A body the CDN ends before the promised length is resumed from the
    next byte; uvicorn refused the short answer before ("Response content
    shorter than Content-Length", 9 times in 48 h of production,
    2026-10-07) and the player saw the file end early."""

    _URL = "https://s-delivery.mxdcontent.example/v/abc.mp4?s=tok"
    _PIECE = 65536  # the first piece goes out before the cut

    @pytest.fixture(autouse=True)
    def _no_waits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hls_proxy, "_FILE_RESUME_WAITS", (0.0, 0.0, 0.0))

    @staticmethod
    def _cdn(
        answers: list[httpx.Response], seen: list[httpx.Request]
    ) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return answers[len(seen) - 1]

        return httpx.MockTransport(handle)

    async def _play(
        self,
        answers: list[httpx.Response],
        *,
        player: dict[str, str] | None = None,
    ) -> tuple[bytes, list[httpx.Request], list[int], list[dict[str, object]]]:
        """The body of a file streamed through the given CDN answers, the
        requests the CDN saw, the bytes reported and the log lines."""
        seen: list[httpx.Request] = []
        sent: list[int] = []
        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=self._cdn(answers, seen)) as client:
                answer = await stream_file(
                    client,
                    self._URL,
                    {"Referer": "https://mixdrop.ag/"},
                    player=player,
                    on_sent=sent.append,
                )
                body = b"".join([chunk async for chunk in answer.chunks])
        return body, seen, sent, logs

    async def test_a_cut_body_is_resumed_once(self) -> None:
        """A transport error mid-body: the file is asked for again from the
        next byte with the same headers, continuing the same answer."""
        answers = [
            httpx.Response(
                200,
                headers={"Content-Type": "video/mp4", "Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 34464]),
            ),
        ]

        body, seen, sent, logs = await self._play(answers)

        assert body == b"a" * self._PIECE + b"b" * 34464
        assert sent == [100000]
        assert len(seen) == 2
        assert seen[1].headers["Range"] == "bytes=65536-99999"
        assert seen[1].headers["Referer"] == "https://mixdrop.ag/"
        assert seen[1].headers["Accept-Encoding"] == "identity"
        assert [log["event"] for log in logs] == ["hls_proxy_file_resumed"]
        assert logs[0]["at"] == self._PIECE

    async def test_a_clean_end_short_of_the_length_is_resumed(self) -> None:
        """The body ends without an error before Content-Length (the
        production case: no transport error in the logs)."""
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_Chunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 34464]),
            ),
        ]

        body, seen, sent, _ = await self._play(answers)

        assert len(body) == 100000
        assert sent == [100000]
        assert seen[1].headers["Range"] == "bytes=65536-99999"

    async def test_a_cut_range_answer_resumes_within_the_range(self) -> None:
        answers = [
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 10000-209999/300000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 75536-209999/300000"},
                stream=_Chunks([b"b" * 134464]),
            ),
        ]

        body, seen, sent, _ = await self._play(
            answers, player={"Range": "bytes=10000-209999"}
        )

        assert len(body) == 200000
        assert sent == [200000]
        assert seen[0].headers["Range"] == "bytes=10000-209999"
        assert seen[1].headers["Range"] == "bytes=75536-209999"

    async def test_a_resume_is_cut_to_the_promised_length(self) -> None:
        """A CDN sending more than asked: the answer ends at the length the
        player was promised."""
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 40000]),
            ),
        ]

        body, _, sent, _ = await self._play(answers)

        assert len(body) == 100000
        assert sent == [100000]

    @pytest.mark.parametrize(
        ("status", "headers"),
        [
            (403, {}),
            (200, {"Content-Length": "100000"}),
            (206, {"Content-Range": "bytes 0-99999/100000"}),
        ],
        ids=["refused", "whole-file", "wrong-start"],
    )
    async def test_a_resume_the_cdn_does_not_honour_cuts_the_transfer(
        self, status: int, headers: dict[str, str]
    ) -> None:
        """Three attempts at the offset, each answered otherwise than with
        the next byte, end the transfer."""
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            *(
                httpx.Response(status, headers=headers, stream=_Chunks([b"b" * 100000]))
                for _ in range(3)
            ),
        ]

        with pytest.raises(FileCut):
            await self._play(answers)

    async def test_a_resume_that_ends_short_is_resumed_again(self) -> None:
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 1000]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 66536-99999/100000"},
                stream=_Chunks([b"c" * 33464]),
            ),
        ]

        body, seen, sent, logs = await self._play(answers)

        assert body == b"a" * self._PIECE + b"b" * 1000 + b"c" * 33464
        assert sent == [100000]
        assert [r.headers["Range"] for r in seen[1:]] == [
            "bytes=65536-99999",
            "bytes=66536-99999",
        ]
        resumed = [log for log in logs if log["event"] == "hls_proxy_file_resumed"]
        assert [log["reason"] for log in resumed] == ["reset", "eof"]

    async def test_a_cut_after_the_last_attempt_is_logged_once(self) -> None:
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 1000]),
            ),
            *(httpx.Response(500) for _ in range(3)),
        ]
        seen: list[httpx.Request] = []
        sent: list[int] = []
        received = 0

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(transport=self._cdn(answers, seen)) as client:
                answer = await stream_file(client, self._URL, {}, on_sent=sent.append)
                with pytest.raises(FileCut):
                    async for chunk in answer.chunks:
                        received += len(chunk)

        assert received == self._PIECE + 1000
        assert sent == [self._PIECE + 1000]
        assert len(seen) == 5
        cut = [log for log in logs if log["event"] == "hls_proxy_file_cut"]
        assert len(cut) == 1
        assert cut[0]["cdn"] == "mxdcontent"
        assert cut[0]["sent"] == self._PIECE + 1000
        assert cut[0]["expected"] == 100000
        assert cut[0]["resumed"] is True
        assert cut[0]["resumes"] == 1

    async def test_a_chunked_answer_passes_no_content_length(self) -> None:
        """With Transfer-Encoding the CDN's Content-Length is not the body's
        length (RFC 9112 section 6.3): it stays out, and the body ends where
        the CDN ends it."""
        answers = [
            httpx.Response(
                200,
                headers={
                    "Content-Type": "video/mp4",
                    "Transfer-Encoding": "chunked",
                    "Content-Length": "100000",
                },
                stream=_Chunks([b"a" * 500]),
            ),
        ]
        seen: list[httpx.Request] = []

        async with httpx.AsyncClient(transport=self._cdn(answers, seen)) as client:
            answer = await stream_file(client, self._URL, {})
            body = b"".join([chunk async for chunk in answer.chunks])

        assert answer.headers == {"content-type": "video/mp4"}
        assert len(body) == 500
        assert len(seen) == 1

    async def test_a_cut_without_a_promised_length_is_not_resumed(self) -> None:
        answers = [
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
        ]

        with pytest.raises(FileCut):
            await self._play(answers)

    async def test_the_opening_request_reads_60_s_and_a_resume_the_stall(
        self,
    ) -> None:
        """The opening request's first byte can take about 30 s (Vinovo); a
        resume reads with the stall timeout, the segments' bound neither."""
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 34464]),
            ),
        ]

        _, seen, _, _ = await self._play(answers)

        assert [r.extensions["timeout"]["read"] for r in seen] == [60.0, 10.0]

    async def test_an_empty_answer_is_not_resumed(self) -> None:
        answers = [
            httpx.Response(200, headers={"Content-Length": "0"}, stream=_Chunks([])),
        ]

        body, seen, sent, _ = await self._play(answers)

        assert body == b""
        assert sent == [0]
        assert len(seen) == 1

    async def test_an_encoded_answer_passes_undecoded(self) -> None:
        """The byte offsets must hold: the body goes out as the CDN sent it,
        with its Content-Encoding."""
        packed = gzip.compress(b"\x00video" * 1000)
        answers = [
            httpx.Response(
                200,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(packed)),
                },
                stream=_Chunks([packed]),
            ),
        ]

        body, _, sent, _ = await self._play(answers)

        assert body == packed
        assert sent == [len(packed)]


def _file_bytes(size: int) -> bytes:
    """A file whose bytes differ from their neighbours' offsets: a byte
    doubled or skipped at a resume changes it."""
    return bytes((i * 31 + (i >> 8) * 7 + (i >> 16)) & 0xFF for i in range(size))


class _FileServer:
    """A local CDN for a file of *size* bytes that answers ranges; each
    connection follows the next of *plans*: ``("rst", n)`` resets the
    connection after n bytes, ``("fin", n)`` closes it after n bytes short
    of the promised length, ``("stall", n)`` sends nothing more after n
    bytes and keeps the connection open, ``("slow", n)`` waits n ms before
    the first byte, ``("late", n)`` waits n ms before the headers and then
    sends nothing, ``("ok", 0)`` serves the whole span. The ranges asked
    for are kept (``None``: no range)."""

    def __init__(self, size: int, plans: list[tuple[str, int]]) -> None:
        self._body = _file_bytes(size)
        self._plans = plans
        self.ranges: list[str | None] = []
        self.url = ""
        self._tasks: set[asyncio.Task[object]] = set()
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> _FileServer:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/v/file.mp4"
        return self

    async def __aexit__(self, *_: object) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        request = (await reader.readuntil(b"\r\n\r\n")).decode()
        asked = [
            line.split("=", 1)[1].split("-", 1)[0]
            for line in request.split("\r\n")
            if line.lower().startswith("range:")
        ]
        how, count = self._plans[min(len(self.ranges), len(self._plans) - 1)]
        self.ranges.append(f"bytes={asked[0]}-" if asked else None)
        size = len(self._body)
        first = int(asked[0]) if asked else 0
        if asked:
            head = (
                b"HTTP/1.1 206 Partial Content\r\n"
                b"Content-Range: bytes %d-%d/%d\r\n" % (first, size - 1, size)
            )
        else:
            head = b"HTTP/1.1 200 OK\r\n"
        if how == "late":
            await asyncio.sleep(count / 1000)
            count = 0
        writer.write(
            head
            + b"Content-Type: video/mp4\r\nContent-Length: %d\r\n\r\n" % (size - first)
        )
        await writer.drain()
        if how == "slow":
            await asyncio.sleep(count / 1000)
        end = size if how in ("ok", "slow") else min(size, first + count)
        writer.write(self._body[first:end])
        await writer.drain()
        if how == "rst":
            sock = writer.get_extra_info("socket")
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            writer.transport.abort()
            return
        if how in ("stall", "late"):
            await asyncio.sleep(30)
        writer.close()


class TestFileResume:
    """A proxied file goes on from the next byte after every interruption of
    its CDN for as long as the answer runs: the VEEV CDN resets a connection
    that stops reading (10 of 10 pauses of 30 to 300 s), and a new range
    answers in 0.25 to 1.3 s (decision 0017)."""

    _URL = "https://s-eri-1.veevcdn.example/v/abc.mp4?t=tok"
    _PIECE = 65536

    @pytest.fixture(autouse=True)
    def _no_waits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hls_proxy, "_FILE_RESUME_WAITS", (0.0, 0.0, 0.0))

    @staticmethod
    def _resumes(telemetry: Telemetry, reason: str, outcome: str) -> float:
        value = telemetry.registry.get_sample_value(
            "scavengarr_file_resume_total",
            {"hoster": "veev", "reason": reason, "outcome": outcome},
        )
        return value or 0.0

    async def _play(
        self,
        answers: list[httpx.Response],
        resume: FileResume | None = None,
        player: dict[str, str] | None = None,
    ) -> tuple[bytes, list[httpx.Request], list[dict[str, object]], Exception | None]:
        """The body through the given CDN answers (as far as it went), the
        requests the CDN saw, the log lines and the error the body raised;
        *player*: the player's request headers."""
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return answers[len(seen) - 1]

        received: list[bytes] = []
        error: Exception | None = None
        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                answer = await stream_file(
                    client,
                    self._URL,
                    {"Referer": "https://veev.example/", "User-Agent": "bound"},
                    player=player,
                    resume=resume,
                )
                try:
                    async for chunk in answer.chunks:
                        received.append(chunk)
                except FileCut as exc:
                    error = exc
        return b"".join(received), seen, logs, error

    def _cut(
        self, first: int, total: int, stream: httpx.AsyncByteStream
    ) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes {first}-{total - 1}/{total}"},
            stream=stream,
        )

    async def test_a_reset_an_early_end_and_a_stall_go_on_byte_for_byte(self) -> None:
        """Against a real socket: each interruption is resumed from the next
        byte, and the player gets exactly the file."""
        size = 600_000
        telemetry = Telemetry()
        plans = [("rst", 150_000), ("fin", 150_000), ("stall", 150_000), ("ok", 0)]
        async with _FileServer(size, plans) as server:
            async with httpx.AsyncClient(timeout=10) as client:
                answer = await stream_file(
                    client,
                    server.url,
                    {},
                    resume=FileResume(
                        stall_timeout=0.3, telemetry=telemetry, hoster="veev"
                    ),
                )
                body = b"".join([chunk async for chunk in answer.chunks])

        assert answer.status == 200
        assert body == _file_bytes(size)
        assert server.ranges[0] is None
        assert len(server.ranges) == 4
        starts = [int(str(r).split("=")[1].rstrip("-")) for r in server.ranges[1:]]
        assert starts == sorted(set(starts))
        for reason in ("reset", "eof", "stall"):
            assert self._resumes(telemetry, reason, "resumed") == 1

    async def test_the_opening_answers_first_byte_is_not_bound_by_the_stall(
        self,
    ) -> None:
        size = 200_000
        async with _FileServer(size, [("slow", 600)]) as server:
            async with httpx.AsyncClient(timeout=10) as client:
                answer = await stream_file(
                    client, server.url, {}, resume=FileResume(stall_timeout=0.3)
                )
                body = b"".join([chunk async for chunk in answer.chunks])

        assert body == _file_bytes(size)
        assert server.ranges == [None]

    async def test_every_interruption_of_one_answer_is_resumed(self) -> None:
        total = 4 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            self._cut(self._PIECE, total, _CutChunks([b"b" * self._PIECE])),
            self._cut(2 * self._PIECE, total, _Chunks([b"c" * self._PIECE])),
            self._cut(3 * self._PIECE, total, _Chunks([b"d" * self._PIECE])),
        ]

        body, seen, logs, error = await self._play(answers)

        assert error is None
        assert (
            body
            == b"a" * self._PIECE
            + b"b" * self._PIECE
            + b"c" * self._PIECE
            + b"d" * self._PIECE
        )
        assert [r.headers["Range"] for r in seen[1:]] == [
            f"bytes={self._PIECE}-{total - 1}",
            f"bytes={2 * self._PIECE}-{total - 1}",
            f"bytes={3 * self._PIECE}-{total - 1}",
        ]
        resumed = [log for log in logs if log["event"] == "hls_proxy_file_resumed"]
        assert [log["reason"] for log in resumed] == ["reset", "reset", "eof"]
        assert [log["attempt"] for log in resumed] == [1, 1, 1]

    async def test_every_request_keeps_the_bound_headers(self) -> None:
        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            self._cut(self._PIECE, total, _Chunks([b"b" * self._PIECE])),
        ]

        _, seen, _, _ = await self._play(answers)

        assert [r.headers["User-Agent"] for r in seen] == ["bound", "bound"]
        assert [r.headers["Referer"] for r in seen] == ["https://veev.example/"] * 2
        assert seen[1].headers["Accept-Encoding"] == "identity"

    async def test_three_failed_attempts_cut_the_file_after_waits_of_0_1_2(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hls_proxy, "_FILE_RESUME_WAITS", (0.0, 1.0, 2.0))
        waits: list[float] = []
        real_sleep = asyncio.sleep

        async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
            if delay:
                waits.append(delay)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        telemetry = Telemetry()
        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            *(httpx.Response(500) for _ in range(3)),
        ]

        body, seen, logs, error = await self._play(
            answers, FileResume(telemetry=telemetry, hoster="veev")
        )

        assert isinstance(error, FileCut)
        assert body == b"a" * self._PIECE
        assert len(seen) == 4
        assert waits == [1.0, 2.0]
        assert self._resumes(telemetry, "reset", "gave_up") == 1
        assert self._resumes(telemetry, "reset", "resumed") == 0
        assert [log["event"] for log in logs].count("hls_proxy_file_cut") == 1

    async def test_the_count_starts_again_once_bytes_flow(self) -> None:
        """Two failed attempts, bytes, then two more failed ones: four
        failures in all, never three at one offset."""
        total = 3 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(500),
            httpx.Response(500),
            self._cut(self._PIECE, total, _CutChunks([b"b" * self._PIECE])),
            httpx.Response(500),
            httpx.Response(500),
            self._cut(2 * self._PIECE, total, _Chunks([b"c" * self._PIECE])),
        ]

        body, seen, _, error = await self._play(answers)

        assert error is None
        assert body == b"a" * self._PIECE + b"b" * self._PIECE + b"c" * self._PIECE
        assert len(seen) == 7

    async def test_a_cdn_that_resets_every_new_connection_ends_after_three(
        self,
    ) -> None:
        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            *(self._cut(self._PIECE, total, _CutChunks([])) for _ in range(3)),
        ]

        _, seen, _, error = await self._play(answers)

        assert isinstance(error, FileCut)
        assert len(seen) == 4

    async def test_a_refused_resume_resolves_again_once(self) -> None:
        refused: list[int] = []

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            refused.append(status)
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {
                "Referer": "https://veev.example/",
                "User-Agent": "bound-again",
            }

        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(403),
            self._cut(self._PIECE, total, _Chunks([b"b" * self._PIECE])),
        ]

        body, seen, _, error = await self._play(answers, FileResume(refresh=_refresh))

        assert error is None
        assert body == b"a" * self._PIECE + b"b" * self._PIECE
        assert refused == [403]
        assert seen[2].url.host == "s-eri-2.veevcdn.example"
        assert seen[2].headers["User-Agent"] == "bound-again"
        assert seen[2].headers["Range"] == f"bytes={self._PIECE}-{total - 1}"

    @pytest.mark.parametrize("status", [404, 410])
    async def test_a_resume_refused_with_404_or_410_resolves_again(
        self, status: int
    ) -> None:
        refused: list[int] = []

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            refused.append(status)
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {}

        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(status),
            self._cut(self._PIECE, total, _Chunks([b"b" * self._PIECE])),
        ]

        body, seen, _, error = await self._play(answers, FileResume(refresh=_refresh))

        assert error is None
        assert body == b"a" * self._PIECE + b"b" * self._PIECE
        assert refused == [status]
        assert seen[2].url.host == "s-eri-2.veevcdn.example"

    async def test_a_server_error_before_a_refusal_leaves_the_new_resolution_to_it(
        self,
    ) -> None:
        """A 500 resolves nothing again; the 403 after it at the same offset
        still gets the one new resolution. The refresh answers as
        ``StremioLinks.after_refusal`` does: nothing for a 500."""
        refused: list[int] = []

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            refused.append(status)
            if status not in (403, 404, 410):
                return None
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {}

        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(500),
            httpx.Response(403),
            self._cut(self._PIECE, total, _Chunks([b"b" * self._PIECE])),
        ]

        body, seen, _, error = await self._play(answers, FileResume(refresh=_refresh))

        assert error is None
        assert body == b"a" * self._PIECE + b"b" * self._PIECE
        assert refused == [403]
        assert seen[3].url.host == "s-eri-2.veevcdn.example"

    async def test_a_resume_of_another_size_after_a_new_resolution_is_not_spliced(
        self,
    ) -> None:
        """The new URL answers from the right byte, but its total names
        another variant of the file: the answer is closed unread and counts
        as a failed attempt; the next one of the opening's size goes on."""

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {}

        total = 2 * self._PIECE
        other = _Chunks([b"x" * self._PIECE])
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(403),
            self._cut(self._PIECE, 3 * self._PIECE, other),
            self._cut(self._PIECE, total, _Chunks([b"b" * self._PIECE])),
        ]

        body, seen, logs, error = await self._play(
            answers, FileResume(refresh=_refresh)
        )

        assert error is None
        assert body == b"a" * self._PIECE + b"b" * self._PIECE
        assert other.closed and not other.read
        assert len(seen) == 4
        resumed = [log for log in logs if log["event"] == "hls_proxy_file_resumed"]
        assert [log["attempt"] for log in resumed] == [2]

    async def test_a_resume_of_another_size_at_the_same_url_is_not_spliced(
        self,
    ) -> None:
        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                206,
                headers={"Content-Range": f"bytes 0-{total - 1}/{total}"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            *(
                self._cut(self._PIECE, 5 * self._PIECE, _Chunks([b"x" * self._PIECE]))
                for _ in range(3)
            ),
        ]

        body, seen, _, error = await self._play(answers)

        assert isinstance(error, FileCut)
        assert body == b"a" * self._PIECE
        assert len(seen) == 4

    async def test_a_second_refusal_at_the_offset_counts_as_an_attempt(self) -> None:
        refused: list[int] = []

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            refused.append(status)
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {}

        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            *(httpx.Response(403) for _ in range(4)),
        ]

        _, seen, _, error = await self._play(answers, FileResume(refresh=_refresh))

        assert isinstance(error, FileCut)
        assert refused == [403]
        assert len(seen) == 5

    async def test_a_refresh_without_a_stream_adds_no_attempt(self) -> None:
        """The new resolution gives no video: the transfer keeps its URL and
        its three attempts, and resolves no second time at the offset."""
        refused: list[int] = []

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            refused.append(status)
            return None

        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            *(httpx.Response(403) for _ in range(3)),
        ]

        _, seen, _, error = await self._play(answers, FileResume(refresh=_refresh))

        assert isinstance(error, FileCut)
        assert refused == [403]
        assert len(seen) == 4
        assert {r.url.host for r in seen} == {"s-eri-1.veevcdn.example"}

    async def test_every_resume_answer_it_does_not_go_on_with_is_closed_unread(
        self,
    ) -> None:
        """A refusal, a server error, the whole file (200) and a range the
        CDN cannot serve (416): each is closed before the next attempt."""

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {}

        total = 2 * self._PIECE
        bodies = [_Chunks([b"x" * self._PIECE]) for _ in range(4)]
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(403, stream=bodies[0]),
            httpx.Response(500, stream=bodies[1]),
            httpx.Response(
                200, headers={"Content-Length": str(total)}, stream=bodies[2]
            ),
            httpx.Response(
                416, headers={"Content-Range": f"bytes */{total}"}, stream=bodies[3]
            ),
        ]

        body, seen, _, error = await self._play(answers, FileResume(refresh=_refresh))

        assert isinstance(error, FileCut)
        assert body == b"a" * self._PIECE
        assert len(seen) == 5
        assert [b.closed for b in bodies] == [True] * 4
        assert [b.read for b in bodies] == [False] * 4

    async def test_a_changed_file_after_a_new_resolution_is_not_spliced(
        self,
    ) -> None:
        """The player's ``If-Range`` goes to the new URL too, and the whole
        file it answers (200: the file changed) is closed unread."""

        async def _refresh(status: int) -> tuple[str, dict[str, str]] | None:
            return "https://s-eri-2.veevcdn.example/v/abc.mp4?t=new", {
                "User-Agent": "bound-again"
            }

        total = 2 * self._PIECE
        changed = [_Chunks([b"x" * total]) for _ in range(3)]
        answers = [
            httpx.Response(
                206,
                headers={"Content-Range": f"bytes 0-{total - 1}/{total}"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(403),
            *(
                httpx.Response(200, headers={"Content-Length": str(total)}, stream=c)
                for c in changed
            ),
        ]

        body, seen, _, error = await self._play(
            answers,
            FileResume(refresh=_refresh),
            player={"Range": "bytes=0-", "If-Range": '"etag"'},
        )

        assert isinstance(error, FileCut)
        assert body == b"a" * self._PIECE
        assert len(seen) == 5
        assert seen[2].url.host == "s-eri-2.veevcdn.example"
        assert seen[2].headers["User-Agent"] == "bound-again"
        assert [r.headers["If-Range"] for r in seen] == ['"etag"'] * 5
        assert [c.closed for c in changed] == [True] * 3
        assert [c.read for c in changed] == [False] * 3

    async def test_a_player_gone_during_a_resumes_wait_ends_it_as_cut(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The player leaves while the resume waits for its second attempt:
        no further request goes out and the stage ends ``cut``."""
        monkeypatch.setattr(hls_proxy, "_FILE_RESUME_WAITS", (0.0, 30.0, 30.0))
        waiting = asyncio.Event()
        real_sleep = asyncio.sleep

        async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
            if delay:
                waiting.set()
            await real_sleep(delay)

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        telemetry = Telemetry()
        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(500),
        ]
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return answers[len(seen) - 1]

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            answer = await stream_file(
                client,
                self._URL,
                {},
                resume=FileResume(telemetry=telemetry, hoster="veev"),
            )

            async def _consume() -> None:
                async for _ in answer.chunks:
                    pass

            task = asyncio.create_task(_consume())
            async with asyncio.timeout(5):
                await waiting.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(seen) == 2
        assert self._resumes(telemetry, "reset", "cut") == 1
        assert self._resumes(telemetry, "reset", "gave_up") == 0

    async def test_a_resume_reads_with_the_stall_timeout(self) -> None:
        total = 2 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            self._cut(self._PIECE, total, _Chunks([b"b" * self._PIECE])),
        ]

        _, seen, _, _ = await self._play(answers, FileResume(stall_timeout=7.0))

        assert [r.extensions["timeout"]["read"] for r in seen] == [60.0, 7.0]

    async def test_a_flowing_resumed_body_is_not_held_to_the_attempts_deadline(
        self,
    ) -> None:
        """After a resume the first byte is due by the attempt's deadline;
        once it came, every later byte gets a stall timeout of its own, so a
        body that keeps flowing for longer than one stall timeout is not
        asked for again."""
        total = 3 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            self._cut(
                self._PIECE,
                total,
                _PacedChunks([b"b" * self._PIECE, b"c" * self._PIECE], 0.5),
            ),
            self._cut(2 * self._PIECE, total, _Chunks([b"c" * self._PIECE])),
        ]

        body, seen, _, error = await self._play(answers, FileResume(stall_timeout=0.8))

        assert error is None
        assert body == b"a" * self._PIECE + b"b" * self._PIECE + b"c" * self._PIECE
        assert len(seen) == 2

    async def test_every_later_resume_keeps_the_bound_headers(self) -> None:
        """The headers a resume goes on with are the request's own: the
        second and third requests still carry the User-Agent and Referer
        the hoster's CDN is bound to (decision 0017)."""
        total = 3 * self._PIECE
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": str(total)},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            self._cut(self._PIECE, total, _CutChunks([b"b" * self._PIECE])),
            self._cut(2 * self._PIECE, total, _Chunks([b"c" * self._PIECE])),
        ]

        _, seen, _, error = await self._play(answers)

        assert error is None
        assert len(seen) == 3
        assert [r.headers["User-Agent"] for r in seen] == ["bound"] * 3
        assert [r.headers["Referer"] for r in seen] == ["https://veev.example/"] * 3
        assert [r.headers["Accept-Encoding"] for r in seen] == ["identity"] * 3

    async def test_the_deadline_bounds_an_attempt_the_read_timeout_cannot(
        self,
    ) -> None:
        """A wait httpx's read timeout does not time (the CDN semaphore, the
        client's retries; here a transport that never answers) ends at the
        attempt's deadline: three attempts, then the cut."""
        seen: list[httpx.Request] = []
        total = 2 * self._PIECE

        async def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(
                    200,
                    headers={"Content-Length": str(total)},
                    stream=_CutChunks([b"a" * self._PIECE]),
                )
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            answer = await stream_file(
                client, self._URL, {}, resume=FileResume(stall_timeout=0.2)
            )
            started = time.monotonic()
            with pytest.raises(FileCut):
                async with asyncio.timeout(5):
                    async for _ in answer.chunks:
                        pass
            elapsed = time.monotonic() - started

        assert len(seen) == 4
        assert elapsed < 2.0

    async def test_an_attempts_answer_and_first_byte_share_one_stall_timeout(
        self,
    ) -> None:
        """A CDN whose resumes answer late and then send nothing: each
        attempt ends one stall timeout after it began, not one for the
        headers and another for the first byte, so the three attempts end
        well before a player's proxy gives up."""
        stall = 0.5
        plans = [("stall", 150_000), ("late", 450)]
        error: Exception | None = None
        async with _FileServer(400_000, plans) as server:
            async with httpx.AsyncClient(timeout=10) as client:
                answer = await stream_file(
                    client, server.url, {}, resume=FileResume(stall_timeout=stall)
                )
                started = time.monotonic()
                try:
                    async for _ in answer.chunks:
                        pass
                except FileCut as exc:
                    error = exc
                elapsed = time.monotonic() - started

        assert isinstance(error, FileCut)
        assert len(server.ranges) == 4
        # the stall and three attempts of one stall timeout each (2.0 s);
        # a stall timeout for the headers and one for the first byte: 3.35 s
        assert elapsed < 2.8


class TestFileResumeRealBounds:
    """The resume's attempts and waits as shipped: nothing patches
    ``_FILE_RESUME_WAITS``, only the clock's sleep is recorded."""

    async def test_three_attempts_after_waits_of_0_1_2_end_within_the_players_60_s(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        waits: list[float] = []
        real_sleep = asyncio.sleep

        async def _sleep(delay: float, *args: object, **kwargs: object) -> None:
            if delay:
                waits.append(delay)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        piece = 65536
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(
                    200,
                    headers={"Content-Length": str(2 * piece)},
                    stream=_CutChunks([b"a" * piece]),
                )
            return httpx.Response(500)

        resume = FileResume()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            answer = await stream_file(
                client, "https://s-eri-1.veevcdn.example/v/a.mp4", {}, resume=resume
            )
            with pytest.raises(FileCut):
                async for _ in answer.chunks:
                    pass

        assert len(seen) == 4
        assert waits == [1.0, 2.0]
        # the stall bound is the setting's default, the 60 s the player's
        # proxy waits for a byte; the decision names 33 s
        assert sum(waits) + 3 * resume.stall_timeout <= 33.0


class TestSegmentCut:
    """A segment the CDN cuts mid-body: resumed once from the next byte when
    the answer is unencoded and promised a length, else cut with one warning.
    The httpx error went out of the body uncaught before (production
    2026-10-12 17:59:20 UTC, acek-cdn: "Exception in ASGI application" with
    httpx's ReadTimeout)."""

    _URL = "https://ab12.acek-cdn.example/hls/seg-3-v1-a1.ts"
    _PIECE = 65536  # the first piece goes out before the cut

    async def _play(
        self, answers: list[httpx.Response], *, head: bool = False
    ) -> tuple[bytes, list[httpx.Request], list[int], list[dict[str, object]]]:
        """The segment's body through the given CDN answers, the requests the
        CDN saw, the bytes reported and the log lines; a cut propagates."""
        seen: list[httpx.Request] = []
        sent: list[int] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return answers[len(seen) - 1]

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handle)
            ) as client:
                answer = await stream_hls_segment(
                    client,
                    self._URL,
                    {"Referer": "https://moflix-stream.example/"},
                    head=head,
                    on_sent=sent.append,
                )
                chunks = answer.chunks
                body = b"".join([chunk async for chunk in chunks])
        return body, seen, sent, logs

    @pytest.mark.parametrize(
        "timed_out", [True, False], ids=["read-timeout", "clean-end-short"]
    )
    async def test_a_cut_segment_is_resumed_once(self, timed_out: bool) -> None:
        first = _TimedOutChunks if timed_out else _Chunks
        answers = [
            httpx.Response(
                200,
                headers={"Content-Type": "video/mp2t", "Content-Length": "100000"},
                stream=first([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 34464]),
            ),
        ]

        body, seen, sent, logs = await self._play(answers)

        assert body == b"a" * self._PIECE + b"b" * 34464
        assert sent == [100000]
        assert seen[1].headers["Range"] == "bytes=65536-99999"
        assert seen[1].headers["Referer"] == "https://moflix-stream.example/"
        assert seen[1].headers["Accept-Encoding"] == "identity"
        assert [log["event"] for log in logs] == ["hls_proxy_segment_resumed"]
        assert logs[0]["cdn"] == "acek-cdn"
        assert logs[0]["at"] == self._PIECE
        assert logs[0]["how"] == "range"

    async def test_a_cdn_that_ignores_the_range_restarts_the_segment(self) -> None:
        """The whole segment again (200 to the ``Range``): the bytes the
        player has are skipped, it gets the body once."""
        whole = b"a" * self._PIECE + b"b" * 34464
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_TimedOutChunks([whole[: self._PIECE]]),
            ),
            httpx.Response(
                200, headers={"Content-Length": "100000"}, stream=_Chunks([whole])
            ),
        ]

        body, seen, sent, logs = await self._play(answers)

        assert body == whole
        assert sent == [100000]
        assert len(seen) == 2
        assert [log["event"] for log in logs] == ["hls_proxy_segment_resumed"]
        assert logs[0]["how"] == "restart"

    async def test_a_cut_is_logged_once_with_the_error(self) -> None:
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_TimedOutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(416),
        ]
        sent: list[int] = []
        received = 0

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: answers.pop(0))
            ) as client:
                answer = await stream_hls_segment(
                    client, self._URL, {}, on_sent=sent.append
                )
                chunks = answer.chunks
                with pytest.raises(SegmentCut):
                    async for chunk in chunks:
                        received += len(chunk)

        assert received == self._PIECE
        assert sent == [self._PIECE]
        cut = [log for log in logs if log["event"] == "hls_proxy_segment_cut"]
        assert len(cut) == 1
        assert cut[0]["cdn"] == "acek-cdn"
        assert cut[0]["error"] == "ReadTimeout"
        assert cut[0]["sent"] == self._PIECE
        assert cut[0]["expected"] == 100000
        assert cut[0]["resumed"] is False

    async def test_an_encoded_segment_is_restarted_past_its_decoded_bytes(
        self,
    ) -> None:
        """``aiter_bytes()`` decodes: the bytes sent are no offsets of the
        CDN's encoded answer, so no range; the segment is asked for again
        from its start and the decoded bytes the player has are skipped."""
        video = bytes(range(256)) * 400
        packed = gzip.compress(video)
        answers = [
            httpx.Response(
                200,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(packed)),
                },
                stream=_TimedOutChunks([packed[: len(packed) // 2]]),
            ),
            httpx.Response(
                200,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(packed)),
                },
                stream=_Chunks([packed]),
            ),
        ]

        body, seen, sent, logs = await self._play(answers)

        assert body == video
        assert sent == [len(video)]
        assert len(seen) == 2
        assert "Range" not in seen[1].headers
        assert [log["event"] for log in logs] == ["hls_proxy_segment_resumed"]
        assert logs[0]["how"] == "restart"
        assert logs[0]["expected"] is None

    async def test_a_chunked_segment_cut_mid_body_is_restarted(self) -> None:
        whole = b"a" * self._PIECE + b"b" * 1000
        answers = [
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_TimedOutChunks([whole[: self._PIECE]]),
            ),
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_Chunks([whole[:1000], whole[1000:]]),
            ),
        ]

        body, seen, sent, logs = await self._play(answers)

        assert body == whole
        assert sent == [len(whole)]
        assert len(seen) == 2
        assert "Range" not in seen[1].headers
        assert seen[1].headers["Referer"] == "https://moflix-stream.example/"
        assert logs[0]["how"] == "restart"

    async def test_a_restart_cut_again_cuts_the_segment(self) -> None:
        answers = [
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_TimedOutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_TimedOutChunks([b"a" * self._PIECE * 2]),
            ),
        ]

        with structlog.testing.capture_logs() as logs:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: answers.pop(0))
            ) as client:
                answer = await stream_hls_segment(client, self._URL, {})
                chunks = answer.chunks
                with pytest.raises(SegmentCut):
                    async for _chunk in chunks:
                        pass

        cut = [log for log in logs if log["event"] == "hls_proxy_segment_cut"]
        assert len(cut) == 1
        assert cut[0]["resumed"] is True
        assert cut[0]["sent"] == self._PIECE * 2

    async def test_a_restart_shorter_than_the_bytes_sent_cuts_the_segment(
        self,
    ) -> None:
        """The CDN's second answer ends before the bytes the player has: no
        clean end, the player asks again."""
        answers = [
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_TimedOutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_Chunks([b"a" * 1000]),
            ),
        ]

        with pytest.raises(SegmentCut):
            await self._play(answers)

    async def test_a_failed_restart_cuts_the_segment(self) -> None:
        answers = [
            httpx.Response(
                200,
                headers={"Transfer-Encoding": "chunked"},
                stream=_TimedOutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(503),
        ]

        with pytest.raises(SegmentCut):
            await self._play(answers)

    async def test_a_segment_reads_with_its_own_stall_bound(self) -> None:
        """The answer, its resume and its restart wait at most
        ``_SEGMENT_READ_TIMEOUT_S`` for a byte, not the client's timeout."""
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_TimedOutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(
                206,
                headers={"Content-Range": "bytes 65536-99999/100000"},
                stream=_Chunks([b"b" * 34464]),
            ),
        ]

        _, seen, _, _ = await self._play(answers)

        assert [r.extensions["timeout"]["read"] for r in seen] == [
            hls_proxy._SEGMENT_READ_TIMEOUT_S
        ] * 2
        assert seen[0].extensions["timeout"]["connect"] == 5.0

    async def test_a_head_request_is_neither_read_nor_resumed(self) -> None:
        stream = _TimedOutChunks([b"a" * self._PIECE])
        answers = [
            httpx.Response(200, headers={"Content-Length": "100000"}, stream=stream)
        ]

        body, seen, sent, logs = await self._play(answers, head=True)

        assert body == b""
        assert sent == [0]
        assert len(seen) == 1
        assert not stream.read
        assert logs == []


class TestSegmentStall:
    """A CDN that falls silent mid-body is cut after the segment's own stall
    bound and resumed, instead of after the client's 30 s (production
    2026-10-12: 10 fetches ahead ended in ``ReadTimeout``, each 30 s without
    a byte)."""

    async def test_a_silent_cdn_is_resumed_after_the_stall_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the bound covers the wait for the headers too: 1 s keeps a loaded
        # runner's stalled loop from failing the test, and stays far from 30 s
        monkeypatch.setattr(hls_proxy, "_SEGMENT_READ_TIMEOUT_S", 1.0)
        whole = bytes(range(256)) * 400
        ranges: list[str] = []
        tasks: set[asyncio.Task[object]] = set()

        async def serve(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            task = asyncio.current_task()
            assert task is not None
            tasks.add(task)
            request = (await reader.readuntil(b"\r\n\r\n")).decode()
            asked = [
                line.split(":", 1)[1].strip()
                for line in request.split("\r\n")
                if line.lower().startswith("range:")
            ]
            if not asked:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(whole) + whole[:65536]
                )
                await writer.drain()
                await asyncio.sleep(30)  # silent: the connection stays open
                return
            ranges.append(asked[0])
            writer.write(
                b"HTTP/1.1 206 Partial Content\r\n"
                b"Content-Range: bytes 65536-%d/%d\r\n"
                b"Content-Length: %d\r\n\r\n"
                % (len(whole) - 1, len(whole), len(whole) - 65536)
                + whole[65536:]
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        started = time.monotonic()
        try:
            with structlog.testing.capture_logs() as logs:
                async with httpx.AsyncClient(timeout=30) as client:
                    answer = await stream_hls_segment(
                        client, f"http://127.0.0.1:{port}/hls/seg-1.ts", {}
                    )
                    chunks = answer.chunks
                    body = b"".join([chunk async for chunk in chunks])
        finally:
            server.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        assert body == whole
        assert ranges == [f"bytes=65536-{len(whole) - 1}"]
        assert time.monotonic() - started < 5
        resumed = [log for log in logs if log["event"] == "hls_proxy_segment_resumed"]
        assert len(resumed) == 1 and resumed[0]["how"] == "range"


class _Chunks(httpx.AsyncByteStream):
    """A response body arriving in the given chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.read = False
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read = True
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        # Closing a CDN answer yields to the loop (httpcore returns the
        # connection): the window in which a second close can arrive
        await asyncio.sleep(0)
        self.closed = True


class _CutChunks(_Chunks):
    """A body the CDN cuts: the given chunks, then the connection dies."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read = True
        for chunk in self._chunks:
            yield chunk
        raise httpx.ReadError("the CDN went away")


class _PacedChunks(_Chunks):
    """A body whose chunks each arrive *gap* seconds after the one before."""

    def __init__(self, chunks: list[bytes], gap: float) -> None:
        super().__init__(chunks)
        self._gap = gap

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read = True
        for chunk in self._chunks:
            await asyncio.sleep(self._gap)
            yield chunk


class _TimedOutChunks(_Chunks):
    """A body the CDN stops sending: the given chunks, then a read timeout
    (production's acek-cdn case)."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read = True
        for chunk in self._chunks:
            yield chunk
        raise httpx.ReadTimeout("the CDN stopped sending")


# ---------------------------------------------------------------------------
# resolve_query_string
# ---------------------------------------------------------------------------


class TestResolveQueryString:
    def test_uses_request_query_when_present(self) -> None:
        result = resolve_query_string(
            "t=abc&expires=123", "https://cdn.example.com/m.m3u8?t=old"
        )
        assert result == "t=abc&expires=123"

    def test_falls_back_to_video_url_query(self) -> None:
        result = resolve_query_string(
            "", "https://cdn.example.com/m.m3u8?t=abc&expires=123"
        )
        assert result == "t=abc&expires=123"

    def test_returns_empty_when_both_empty(self) -> None:
        result = resolve_query_string("", "https://cdn.example.com/m.m3u8")
        assert result == ""

    def test_handles_none_like_empty(self) -> None:
        # request.url.query could be an empty string
        result = resolve_query_string(
            "", "https://cdn.example.com/hls/master.m3u8?token=xyz"
        )
        assert result == "token=xyz"

    def test_request_query_takes_priority(self) -> None:
        result = resolve_query_string("new=1", "https://cdn.example.com/m.m3u8?old=2")
        assert result == "new=1"
