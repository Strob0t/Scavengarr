"""Tests for the HLS proxy helpers (manifest rewriting + CDN fetch)."""

from __future__ import annotations

import gzip
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.infrastructure.stremio import hls_proxy
from scavengarr.infrastructure.stremio.hls_proxy import (
    build_cdn_url,
    cdn_base_from_url,
    fetch_hls_resource,
    rewrite_manifest,
    stream_hls_segment,
)
from scavengarr.interfaces.api.stremio.router import _resolve_query_string


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


class _Chunks(httpx.AsyncByteStream):
    """A response body arriving in the given chunks."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


# ---------------------------------------------------------------------------
# _resolve_query_string (router helper)
# ---------------------------------------------------------------------------


class TestResolveQueryString:
    def test_uses_request_query_when_present(self) -> None:
        result = _resolve_query_string(
            "t=abc&expires=123", "https://cdn.example.com/m.m3u8?t=old"
        )
        assert result == "t=abc&expires=123"

    def test_falls_back_to_video_url_query(self) -> None:
        result = _resolve_query_string(
            "", "https://cdn.example.com/m.m3u8?t=abc&expires=123"
        )
        assert result == "t=abc&expires=123"

    def test_returns_empty_when_both_empty(self) -> None:
        result = _resolve_query_string("", "https://cdn.example.com/m.m3u8")
        assert result == ""

    def test_handles_none_like_empty(self) -> None:
        # request.url.query could be an empty string
        result = _resolve_query_string(
            "", "https://cdn.example.com/hls/master.m3u8?token=xyz"
        )
        assert result == "token=xyz"

    def test_request_query_takes_priority(self) -> None:
        result = _resolve_query_string("new=1", "https://cdn.example.com/m.m3u8?old=2")
        assert result == "new=1"
