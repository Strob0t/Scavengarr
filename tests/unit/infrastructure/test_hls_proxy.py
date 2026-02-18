"""Tests for the HLS proxy helpers (manifest rewriting + CDN fetch)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.stremio import hls_proxy
from scavengarr.infrastructure.stremio.hls_proxy import (
    build_cdn_url,
    cdn_base_from_url,
    fetch_hls_resource,
    rewrite_manifest,
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
    def test_master_manifest_relative_urls_unchanged(self) -> None:
        cdn_base = "https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/"
        proxy_base = "http://localhost:7979/api/v1/stremio/proxy/abc123/"
        result = rewrite_manifest(_MASTER_MANIFEST, cdn_base, proxy_base)
        # Relative URLs should remain untouched
        assert "index-v1-a1.m3u8?t=abc123" in result
        assert cdn_base not in result or result == _MASTER_MANIFEST

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


# ---------------------------------------------------------------------------
# fetch_hls_resource
# ---------------------------------------------------------------------------


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
