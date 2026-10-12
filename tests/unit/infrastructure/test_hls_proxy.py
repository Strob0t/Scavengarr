"""Tests for the HLS proxy helpers (manifest rewriting + CDN fetch)."""

from __future__ import annotations

import asyncio
import gc
import gzip
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
import structlog

from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.stremio import hls_proxy
from scavengarr.infrastructure.stremio.hls_proxy import (
    FileCut,
    build_cdn_url,
    cdn_base_from_url,
    fetch_hls_resource,
    resolve_query_string,
    rewrite_manifest,
    stream_file,
    stream_hls_segment,
)


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> None:
    """Clear the module-level manifest cache between tests."""
    hls_proxy._manifest_cache.clear()


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
        for a token bound to the Pi (step 27, 2026-10-07)."""
        cdn_base = "https://2ZO6sb3MYz7fapc.acek-cdn.com/hls2/01/abc_h/"
        proxy_base = "https://scavengarr.lan/api/v1/stremio/proxy/sid/"
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
        proxy_base = "https://scavengarr.lan/api/v1/stremio/proxy/sid/"
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
            chunks, _ = await stream_hls_segment(client, url, {})
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
            chunks, _ = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
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
            chunks, content_type = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, head=True
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
            chunks, _ = await stream_hls_segment(client, "https://cdn.test/1.ts", {})
            received = b"".join([chunk async for chunk in chunks])

        assert received == data

    async def test_the_bytes_sent_are_reported_when_the_body_ends(self) -> None:
        sent: list[int] = []

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=_Chunks([b"\x47" * 70000]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            chunks, _ = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, on_sent=sent.append
            )
            [chunk async for chunk in chunks]

        assert sent == [70000]

    async def test_a_body_closed_early_reports_the_bytes_so_far(self) -> None:
        sent: list[int] = []
        body = _Chunks([b"\x47" * 65536, b"\x47" * 1000])

        def _cdn(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(_cdn)) as client:
            chunks, _ = await stream_hls_segment(
                client, "https://cdn.test/1.ts", {}, on_sent=sent.append
            )
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
                    chunks, _ = await stream_hls_segment(
                        client, "https://cdn.test/1.ts", {}, on_sent=sent.append
                    )
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
    """A body the CDN ends before the promised length is resumed once from
    the next byte; uvicorn refused the short answer before ("Response
    content shorter than Content-Length", 9 times in 48 h of production,
    2026-10-07) and the player saw the file end early."""

    _URL = "https://s-delivery.mxdcontent.example/v/abc.mp4?s=tok"
    _PIECE = 65536  # the first piece goes out before the cut

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
        answers = [
            httpx.Response(
                200,
                headers={"Content-Length": "100000"},
                stream=_CutChunks([b"a" * self._PIECE]),
            ),
            httpx.Response(status, headers=headers, stream=_Chunks([b"b" * 100000])),
        ]

        with pytest.raises(FileCut):
            await self._play(answers)

    async def test_a_resume_that_ends_short_cuts_the_transfer(self) -> None:
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
        assert len(seen) == 2
        cut = [log for log in logs if log["event"] == "hls_proxy_file_cut"]
        assert len(cut) == 1
        assert cut[0]["cdn"] == "mxdcontent"
        assert cut[0]["sent"] == self._PIECE + 1000
        assert cut[0]["expected"] == 100000
        assert cut[0]["resumed"] is True

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
