"""Tests for shared verify_video_url helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers._verify import (
    is_error_redirect,
    verify_video_url,
)


class TestVerifyVideoUrl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 206])
    async def test_returns_true_on_success(self, status: int) -> None:
        head_resp = MagicMock()
        head_resp.status_code = status

        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(return_value=head_resp)

        result = await verify_video_url(
            client,
            "https://cdn.example.com/video.mp4",
            {"Referer": "https://example.com/"},
            "test",
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
            client,
            "https://cdn.example.com/video.mp4",
            {"Referer": "https://example.com/"},
            "test",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        result = await verify_video_url(
            client,
            "https://cdn.example.com/video.mp4",
            {"Referer": "https://example.com/"},
            "test",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_passes_headers_and_follows_redirects(self) -> None:
        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.head = AsyncMock(return_value=head_resp)

        headers = {"Referer": "https://example.com/", "Authorization": "Bearer tok"}
        await verify_video_url(
            client, "https://cdn.example.com/video.mp4", headers, "test"
        )

        client.head.assert_awaited_once()
        _, kwargs = client.head.call_args
        assert kwargs["headers"] == headers
        assert kwargs["follow_redirects"] is True
        assert kwargs["timeout"] == 8.0


class TestIsErrorRedirect:
    @pytest.mark.parametrize(
        "url",
        [
            "https://host.example/404",
            "https://host.example/404.html",
            "https://host.example/error",
            "https://host.example/error?code=1",
            "https://host.example/errors/not-found",
            "https://host.example/?error=file_removed",
        ],
    )
    def test_error_pages(self, url: str) -> None:
        assert is_error_redirect(url)

    @pytest.mark.parametrize(
        "url",
        [
            # "error" inside a title must not count ("The Terror")
            "https://katfile.com/abcdefghijkl/The.Terror.S01E01.mkv.html",
            "https://serienstream.to/serie/stream/the-terror",
            "https://host.example/e/abc404def",
            "https://host.example/v/Errors.and.Omissions.2019.mkv",
        ],
    )
    def test_file_pages(self, url: str) -> None:
        assert not is_error_redirect(url)
