"""Tests for VidkingResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.vidking import (
    VidkingResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert _extract_file_id("https://vidking.net/abc123def") == "abc123def"

    def test_e_prefix(self) -> None:
        assert _extract_file_id("https://vidking.net/e/abc123def") == "abc123def"

    def test_d_prefix(self) -> None:
        assert _extract_file_id("https://vidking.net/d/abc123def") == "abc123def"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.vidking.net/abc123def") == "abc123def"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://vidking.net/abc123def") == "abc123def"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><video>Player</video></body></html>"
_OFFLINE_PAGE = "<html><body>Video not found</body></html>"


class TestVidkingResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert VidkingResolver(http_client=client).name == "vidking"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://vidking.net/abc123def"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://vidking.net/abc123def"
        result = await VidkingResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://vidking.net/abc123def"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidkingResolver(http_client=client).resolve(
            "https://vidking.net/abc123def"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        result = await VidkingResolver(http_client=client).resolve(
            "https://vidking.net/abc123def"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))
        result = await VidkingResolver(http_client=client).resolve(
            "https://vidking.net/abc123def"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        result = await VidkingResolver(http_client=client).resolve(
            "https://example.com/abc123def"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://vidking.net/404"
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        result = await VidkingResolver(http_client=client).resolve(
            "https://vidking.net/abc123def"
        )
        assert result is None
