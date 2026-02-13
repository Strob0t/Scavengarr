"""Tests for FastpicResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.fastpic import (
    FastpicResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_org_domain(self) -> None:
        url = "https://fastpic.org/view/123/2025/0101/abcdef01234567890abcdef012345678.jpg.html"
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.jpg"

    def test_ru_domain(self) -> None:
        url = (
            "https://fastpic.ru/fullview/123/2025/abcdef01234567890abcdef012345678.png"
        )
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.png"

    def test_www_prefix(self) -> None:
        url = "https://www.fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.jpg"

    def test_http_scheme(self) -> None:
        url = "http://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.jpg"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/view/123/abcdef.jpg") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://fastpic.org/upload/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><img src='image.jpg'></body></html>"
_OFFLINE_PAGE = "<html><body>404 Not Found</body></html>"


class TestFastpicResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert FastpicResolver(http_client=client).name == "fastpic"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = (
            "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        )

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        result = await FastpicResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = (
            "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        )

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await FastpicResolver(http_client=client).resolve(
            "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await FastpicResolver(http_client=client).resolve(
            "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        result = await FastpicResolver(http_client=client).resolve(
            "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        result = await FastpicResolver(http_client=client).resolve(
            "https://example.com/view/123/abc.jpg"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://fastpic.org/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await FastpicResolver(http_client=client).resolve(
            "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        )
        assert result is None
