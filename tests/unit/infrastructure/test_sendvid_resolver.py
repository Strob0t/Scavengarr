"""Tests for SendVidResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.sendvid import (
    SendVidResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_url(self) -> None:
        assert _extract_file_id("https://sendvid.com/abc123") == "abc123"

    def test_embed_url(self) -> None:
        assert _extract_file_id("https://sendvid.com/embed/abc123") == "abc123"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.sendvid.com/abc123") == "abc123"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://sendvid.com/abc123") == "abc123"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None

    def test_no_file_id(self) -> None:
        assert _extract_file_id("https://sendvid.com/") is None


class TestSendVidResolver:
    def test_name(self) -> None:
        resolver = SendVidResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "sendvid"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://sendvid.com/abc123"
        respx.get("https://sendvid.com/api/v1/videos/abc123/status.json").respond(
            200, json={"status": "ok"},
        )
        respx.get("https://sendvid.com/abc123").respond(
            200, text='<video><source src="https://cdn.sendvid.com/v.mp4"></video>',
        )

        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_api_404(self) -> None:
        url = "https://sendvid.com/abc123"
        respx.get("https://sendvid.com/api/v1/videos/abc123/status.json").respond(404)

        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_api_error(self) -> None:
        url = "https://sendvid.com/abc123"
        respx.get("https://sendvid.com/api/v1/videos/abc123/status.json").respond(500)

        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_page_error(self) -> None:
        url = "https://sendvid.com/abc123"
        respx.get("https://sendvid.com/api/v1/videos/abc123/status.json").respond(
            200, json={"status": "ok"},
        )
        respx.get("https://sendvid.com/abc123").respond(404)

        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://sendvid.com/abc123"
        respx.get("https://sendvid.com/api/v1/videos/abc123/status.json").mock(
            side_effect=httpx.ConnectError("failed"),
        )

        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(
                "https://example.com/abc123",
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_embed_url(self) -> None:
        url = "https://sendvid.com/embed/xyz789"
        respx.get("https://sendvid.com/api/v1/videos/xyz789/status.json").respond(
            200, json={"status": "ok"},
        )
        respx.get("https://sendvid.com/xyz789").respond(
            200, text="<video><source src='https://cdn.sendvid.com/v.mp4'></video>",
        )

        async with httpx.AsyncClient() as client:
            result = await SendVidResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url
