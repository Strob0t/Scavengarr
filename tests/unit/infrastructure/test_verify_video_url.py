"""Tests for shared verify_video_url helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers._verify import verify_video_url


class TestVerifyVideoUrl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 206])
    async def test_returns_true_on_success(self, status: int) -> None:
        head_resp = MagicMock()
        head_resp.status_code = status

        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(return_value=head_resp)

        result = await verify_video_url(
            client, "https://cdn.example.com/video.mp4", {"Referer": "https://example.com/"}, "test"
        )
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [403, 404, 500, 503])
    async def test_returns_false_on_error_status(self, status: int) -> None:
        head_resp = MagicMock()
        head_resp.status_code = status

        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(return_value=head_resp)

        result = await verify_video_url(
            client, "https://cdn.example.com/video.mp4", {"Referer": "https://example.com/"}, "test"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        result = await verify_video_url(
            client, "https://cdn.example.com/video.mp4", {"Referer": "https://example.com/"}, "test"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_passes_headers_and_follows_redirects(self) -> None:
        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(return_value=head_resp)

        headers = {"Referer": "https://example.com/", "Authorization": "Bearer tok"}
        await verify_video_url(client, "https://cdn.example.com/video.mp4", headers, "test")

        client.head.assert_awaited_once()
        _, kwargs = client.head.call_args
        assert kwargs["headers"] == headers
        assert kwargs["follow_redirects"] is True
        assert kwargs["timeout"] == 8.0
