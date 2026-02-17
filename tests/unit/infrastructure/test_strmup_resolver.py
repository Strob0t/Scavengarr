"""Tests for StrmupResolver (StreamUp / strmup)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.strmup import (
    StrmupResolver,
    _extract_file_id,
)

_HLS_URL = "https://cdn.strmup.to/hls/abc123/master.m3u8"


class TestExtractFileId:
    def test_strmup_to(self) -> None:
        assert _extract_file_id("https://strmup.to/abc1234567890") == "abc1234567890"

    def test_streamup_ws(self) -> None:
        assert _extract_file_id("https://streamup.ws/xyz9876543210") == "xyz9876543210"

    def test_streamup_cc(self) -> None:
        assert _extract_file_id("https://streamup.cc/abc1234567890") == "abc1234567890"

    def test_v_prefix(self) -> None:
        assert _extract_file_id("https://strmup.to/v/abc1234567890") == "abc1234567890"

    def test_www_prefix(self) -> None:
        assert (
            _extract_file_id("https://www.strmup.to/abc1234567890") == "abc1234567890"
        )

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://strmup.to/abc1234567890") == "abc1234567890"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc1234567890") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://strmup.to/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


class TestStrmupResolver:
    def test_name(self) -> None:
        resolver = StrmupResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "strmup"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_hls_from_page(self) -> None:
        url = "https://strmup.to/abc1234567890"
        html = f"""
        <html><head><title>Test Video</title></head>
        <body>
        <script>
        var player = {{
            streaming_url: "{_HLS_URL}"
        }};
        </script>
        </body></html>
        """
        respx.get("https://strmup.to/abc1234567890").respond(200, text=html)

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _HLS_URL
        assert result.is_hls is True
        assert result.quality == StreamQuality.UNKNOWN

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_hls_from_ajax_fallback(self) -> None:
        url = "https://strmup.to/abc1234567890"
        html = "<html><head><title>Test Video - Some Long Title Here</title></head><body><div>no streaming url here but enough content to pass blank check</div></body></html>"
        ajax_data = {"streaming_url": _HLS_URL}

        respx.get("https://strmup.to/abc1234567890").respond(200, text=html)
        respx.get("https://strmup.to/ajax/stream?filecode=abc1234567890").respond(
            200, json=ajax_data
        )

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _HLS_URL
        assert result.is_hls is True

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_404(self) -> None:
        url = "https://strmup.to/abc1234567890"
        respx.get("https://strmup.to/abc1234567890").respond(404)

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_blank_page(self) -> None:
        url = "https://strmup.to/abc1234567890"
        respx.get("https://strmup.to/abc1234567890").respond(200, text="<html></html>")

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://strmup.to/abc1234567890"
        respx.get("https://strmup.to/abc1234567890").mock(
            side_effect=httpx.ConnectError("refused")
        )

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve("https://example.com/abc1234567890")

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_when_no_hls_found(self) -> None:
        url = "https://strmup.to/abc1234567890"
        html = "<html><head><title>Test Video - Some Long Title Here</title></head><body><div>no video here at all just text content that is long enough</div></body></html>"
        ajax_data = {"error": "not found"}

        respx.get("https://strmup.to/abc1234567890").respond(200, text=html)
        respx.get("https://strmup.to/ajax/stream?filecode=abc1234567890").respond(
            200, json=ajax_data
        )

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_500(self) -> None:
        url = "https://strmup.to/abc1234567890"
        respx.get("https://strmup.to/abc1234567890").respond(500)

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_single_quote_streaming_url(self) -> None:
        url = "https://strmup.to/abc1234567890"
        html = f"""
        <html><head><title>Test</title></head>
        <body><script>
        streaming_url: '{_HLS_URL}'
        </script></body></html>
        """
        respx.get("https://strmup.to/abc1234567890").respond(200, text=html)

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == _HLS_URL

    @respx.mock
    @pytest.mark.asyncio()
    async def test_ajax_fallback_network_error(self) -> None:
        """AJAX fallback fails gracefully on network error."""
        url = "https://strmup.to/abc1234567890"
        html = "<html><head><title>Test Video - Long Title</title></head><body><div>no streaming url in the page content but enough text to pass blank check</div></body></html>"

        respx.get("https://strmup.to/abc1234567890").respond(200, text=html)
        respx.get("https://strmup.to/ajax/stream?filecode=abc1234567890").mock(
            side_effect=httpx.ConnectError("fail")
        )

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None
