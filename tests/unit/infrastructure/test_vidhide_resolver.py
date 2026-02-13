"""Tests for VidhideResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.vidhide import (
    VidhideResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert _extract_file_id("https://vidhide.com/abc123def456") == "abc123def456"

    def test_filelions(self) -> None:
        assert _extract_file_id("https://filelions.to/f/abc123def456") == "abc123def456"

    def test_vidhidepro(self) -> None:
        assert _extract_file_id("https://vidhidepro.com/abc123def456") == "abc123def456"

    def test_embed_prefix(self) -> None:
        assert _extract_file_id("https://vidhide.com/embed-abc123def456.html") == "abc123def456"

    def test_e_prefix(self) -> None:
        assert _extract_file_id("https://vidhide.com/e/abc123def456") == "abc123def456"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.vidhide.com/abc123def456") == "abc123def456"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://vidhide.com/abc123def456") == "abc123def456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://vidhide.com/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><video>Player</video></body></html>"
_OFFLINE_PAGE = "<html><body><h1>File Not Found</h1></body></html>"


class TestVidhideResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert VidhideResolver(http_client=client).name == "vidhide"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://vidhide.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://vidhide.com/abc123def456"
        result = await VidhideResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://vidhide.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidhideResolver(http_client=client).resolve(
            "https://vidhide.com/abc123def456"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidhideResolver(http_client=client).resolve(
            "https://vidhide.com/abc123def456"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        result = await VidhideResolver(http_client=client).resolve(
            "https://vidhide.com/abc123def456"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        result = await VidhideResolver(http_client=client).resolve(
            "https://example.com/abc123def456"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://vidhide.com/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidhideResolver(http_client=client).resolve(
            "https://vidhide.com/abc123def456"
        )
        assert result is None
