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
        html = (
            "<html><head><title>Test Video - Some Long Title"
            "</title></head><body><div>no streaming url here"
            " but enough content to pass blank check"
            "</div></body></html>"
        )
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
        html = (
            "<html><head><title>Test Video - Some Long Title"
            "</title></head><body><div>no video here at all"
            " just text content that is long enough"
            "</div></body></html>"
        )
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
        html = (
            "<html><head><title>Test Video - Long Title"
            "</title></head><body><div>no streaming url in"
            " the page content but enough text to pass"
            " blank check</div></body></html>"
        )

        respx.get("https://strmup.to/abc1234567890").respond(200, text=html)
        respx.get("https://strmup.to/ajax/stream?filecode=abc1234567890").mock(
            side_effect=httpx.ConnectError("fail")
        )

        async with httpx.AsyncClient() as client:
            resolver = StrmupResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is None


class TestVidara:
    """vidara.so / vidaraa.cc: JSON API ``POST /api/stream`` (JD2 VidaraTo)."""

    _HLS = "https://s8-t25.97bf1.com/hls/W2OF/master.m3u8?token=t"

    @pytest.mark.parametrize(
        "url",
        [
            "https://vidara.so/e/7Ba8NtMpSl0Wv",
            "https://vidaraa.cc/e/7Ba8NtMpSl0Wv",
            "https://vidara.to/v/7Ba8NtMpSl0Wv",
        ],
    )
    def test_file_id(self, url: str) -> None:
        assert _extract_file_id(url) == "7Ba8NtMpSl0Wv"

    def test_vidaraa_is_dispatched(self) -> None:
        resolver = StrmupResolver(http_client=httpx.AsyncClient())
        assert "vidaraa" in resolver.supported_domains

    @respx.mock
    async def test_resolves_via_api(self) -> None:
        api = respx.post("https://vidaraa.cc/api/stream").respond(
            200, json={"filecode": "7Ba8NtMpSl0Wv", "streaming_url": self._HLS}
        )

        async with httpx.AsyncClient() as client:
            result = await StrmupResolver(http_client=client).resolve(
                "https://vidaraa.cc/e/7Ba8NtMpSl0Wv"
            )

        assert result is not None
        assert result.video_url == self._HLS
        assert result.is_hls is True
        assert result.quality == StreamQuality.UNKNOWN
        body = json.loads(api.calls.last.request.content)
        assert body == {"device": "web", "filecode": "7Ba8NtMpSl0Wv"}

    @respx.mock
    async def test_video_not_found(self) -> None:
        respx.post("https://vidara.so/api/stream").respond(
            404, json={"error": "Video not found"}
        )

        async with httpx.AsyncClient() as client:
            result = await StrmupResolver(http_client=client).resolve(
                "https://vidara.so/e/7Ba8NtMpSl0Wv"
            )

        assert result is None

    def test_kinoger_player_is_a_vidara_host(self) -> None:
        """kinoger.pw (kinoger's player tab) runs Vidara under its own name:
        the page credits "Vidara" and calls ``POST /api/stream``."""
        resolver = StrmupResolver(http_client=httpx.AsyncClient())
        assert "kinoger" in resolver.supported_domains
        assert _extract_file_id("https://kinoger.pw/e/5wCjBALU9QDHF") == (
            "5wCjBALU9QDHF"
        )

    @respx.mock
    async def test_kinoger_player_resolves_via_api(self) -> None:
        api = respx.post("https://kinoger.pw/api/stream").respond(
            200, json={"filecode": "5wCjBALU9QDHF", "streaming_url": self._HLS}
        )

        async with httpx.AsyncClient() as client:
            result = await StrmupResolver(http_client=client).resolve(
                "https://kinoger.pw/e/5wCjBALU9QDHF"
            )

        assert result is not None
        assert result.video_url == self._HLS
        body = json.loads(api.calls.last.request.content)
        assert body == {"device": "web", "filecode": "5wCjBALU9QDHF"}

    @respx.mock
    async def test_api_without_streaming_url(self) -> None:
        respx.post("https://vidara.so/api/stream").respond(200, json={"title": "x"})

        async with httpx.AsyncClient() as client:
            result = await StrmupResolver(http_client=client).resolve(
                "https://vidara.so/e/7Ba8NtMpSl0Wv"
            )

        assert result is None
