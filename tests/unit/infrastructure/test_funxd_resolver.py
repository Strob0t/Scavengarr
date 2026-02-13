"""Tests for FunxdResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.funxd import (
    FunxdResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert _extract_file_id("https://funxd.site/abc123def456") == "abc123def456"

    def test_e_prefix(self) -> None:
        assert _extract_file_id("https://funxd.site/e/abc123def456") == "abc123def456"

    def test_d_prefix(self) -> None:
        assert _extract_file_id("https://funxd.site/d/abc123def456") == "abc123def456"

    def test_with_html(self) -> None:
        assert _extract_file_id("https://funxd.site/abc123def456.html") == "abc123def456"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.funxd.site/abc123def456") == "abc123def456"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://funxd.site/abc123def456") == "abc123def456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://funxd.site/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><h4>Movie.mkv</h4></body></html>"
_OFFLINE_PAGE = "<html><body><h1>File Not Found</h1></body></html>"


class TestFunxdResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert FunxdResolver(http_client=client).name == "funxd"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://funxd.site/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://funxd.site/abc123def456"
        result = await FunxdResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://funxd.site/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await FunxdResolver(http_client=client).resolve(
            "https://funxd.site/abc123def456"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        result = await FunxdResolver(http_client=client).resolve(
            "https://funxd.site/abc123def456"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))
        result = await FunxdResolver(http_client=client).resolve(
            "https://funxd.site/abc123def456"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        result = await FunxdResolver(http_client=client).resolve(
            "https://example.com/abc123def456"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://funxd.site/404"
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        result = await FunxdResolver(http_client=client).resolve(
            "https://funxd.site/abc123def456"
        )
        assert result is None
