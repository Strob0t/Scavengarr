"""Tests for VidozaResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.vidoza import (
    VidozaResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert _extract_file_id("https://vidoza.net/abc123def456.html") == "abc123def456"

    def test_videzz(self) -> None:
        assert _extract_file_id("https://videzz.net/abc123def456.html") == "abc123def456"

    def test_embed_prefix(self) -> None:
        url = "https://vidoza.net/embed-abc123def456.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_without_html(self) -> None:
        assert _extract_file_id("https://vidoza.net/abc123def456") == "abc123def456"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.vidoza.net/abc123def456.html") == "abc123def456"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://vidoza.net/abc123def456.html") == "abc123def456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456.html") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://vidoza.net/abc123.html") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><video>Player</video></body></html>"
_OFFLINE_PAGE = "<html><body><h1>File Not Found</h1></body></html>"


class TestVidozaResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert VidozaResolver(http_client=client).name == "vidoza"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://vidoza.net/abc123def456.html"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://vidoza.net/abc123def456.html"
        result = await VidozaResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://vidoza.net/abc123def456.html"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidozaResolver(http_client=client).resolve(
            "https://vidoza.net/abc123def456.html"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidozaResolver(http_client=client).resolve(
            "https://vidoza.net/abc123def456.html"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        result = await VidozaResolver(http_client=client).resolve(
            "https://vidoza.net/abc123def456.html"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        result = await VidozaResolver(http_client=client).resolve(
            "https://example.com/abc123def456.html"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://vidoza.net/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await VidozaResolver(http_client=client).resolve(
            "https://vidoza.net/abc123def456.html"
        )
        assert result is None
