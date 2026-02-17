"""Tests for VidsonicResolver (Vidsonic)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.vidsonic import (
    VidsonicResolver,
    _decode_hex_blob,
    _extract_file_id,
)

_HLS_URL = "https://st-us-01.vidsonic.net/secure/98/abc123def456/master.m3u8"

# Build hex blob from URL: reverse → hex-encode → pipe-delimit
_REVERSED = _HLS_URL[::-1]
_HEX_CHARS = "".join(f"{ord(c):02x}" for c in _REVERSED)
# Split into 10-char pipe-delimited segments
_SEGMENTS = [_HEX_CHARS[i : i + 10] for i in range(0, len(_HEX_CHARS), 10)]
_HEX_BLOB = "|".join(_SEGMENTS)


def _make_page(blob: str) -> str:
    """Build a minimal Vidsonic embed page with the hex blob."""
    return (
        "<html><head><title>Video Player</title></head>"
        "<body>"
        "<video id='player'></video>"
        "<script>"
        f"const _0x1 = '{blob}';"
        "const _0x2 = function(_0x3) {"
        "const _0x4 = _0x3.split('|').join('');"
        "let _0x5 = '';"
        "for (let _0x6 = 0; _0x6 < _0x4.length; _0x6 += 2) {"
        "_0x5 += String.fromCharCode("
        "parseInt(_0x4.substr(_0x6, 2), 16));"
        "} return _0x5.split('').reverse().join(''); };"
        "const _0x7 = _0x2(_0x1);"
        "player.src({ src: _0x7, type: 'application/x-mpegURL' });"
        "</script>"
        "</body></html>"
    )


class TestExtractFileId:
    def test_embed_url(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_download_url(self) -> None:
        url = "https://vidsonic.net/d/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_bare_path(self) -> None:
        url = "https://vidsonic.net/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        url = "https://www.vidsonic.net/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_http_scheme(self) -> None:
        url = "http://vidsonic.net/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/e/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://vidsonic.net/e/abc123") is None

    def test_uppercase_rejected(self) -> None:
        assert _extract_file_id("https://vidsonic.net/e/ABC123DEF456") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


class TestDecodeHexBlob:
    def test_decodes_valid_blob(self) -> None:
        result = _decode_hex_blob(_HEX_BLOB)
        assert result == _HLS_URL

    def test_returns_none_for_non_url(self) -> None:
        # Hex that decodes to non-http text
        text = "hello world"
        reversed_text = text[::-1]
        hex_str = "".join(f"{ord(c):02x}" for c in reversed_text)
        assert _decode_hex_blob(hex_str) is None

    def test_returns_none_for_invalid_hex(self) -> None:
        assert _decode_hex_blob("zzzz|xxxx") is None

    def test_returns_none_for_empty(self) -> None:
        assert _decode_hex_blob("") is None


class TestVidsonicResolver:
    def test_name(self) -> None:
        resolver = VidsonicResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "vidsonic"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_hls_from_page(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        html = _make_page(_HEX_BLOB)
        respx.get("https://vidsonic.net/e/abc123def456").respond(
            200, text=html
        )

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _HLS_URL
        assert result.is_hls is True
        assert result.quality == StreamQuality.UNKNOWN

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_video_not_found(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        html = (
            "<html><head><title>Not Found</title></head>"
            "<body><h1>Video Not Found</h1></body></html>"
        )
        respx.get("https://vidsonic.net/e/abc123def456").respond(
            200, text=html
        )

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_video_id_required(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        html = (
            "<html><body>"
            "<p>Video ID is required</p>"
            "</body></html>"
        )
        respx.get("https://vidsonic.net/e/abc123def456").respond(
            200, text=html
        )

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_404(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        respx.get("https://vidsonic.net/e/abc123def456").respond(404)

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        respx.get("https://vidsonic.net/e/abc123def456").mock(
            side_effect=httpx.ConnectError("refused")
        )

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(
                "https://example.com/e/abc123def456"
            )

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_when_no_hex_blob(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        html = (
            "<html><head><title>Player</title></head>"
            "<body><video></video>"
            "<script>// no hex blob here</script>"
            "</body></html>"
        )
        respx.get("https://vidsonic.net/e/abc123def456").respond(
            200, text=html
        )

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_500(self) -> None:
        url = "https://vidsonic.net/e/abc123def456"
        respx.get("https://vidsonic.net/e/abc123def456").respond(500)

        async with httpx.AsyncClient() as client:
            resolver = VidsonicResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None
