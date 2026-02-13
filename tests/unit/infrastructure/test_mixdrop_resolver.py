"""Tests for MixdropResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.mixdrop import (
    MixdropResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain_f(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/f/abc123") == "abc123"

    def test_main_domain_e(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/e/abc123") == "abc123"

    def test_main_domain_emb(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/emb/abc123") == "abc123"

    def test_mxdrop(self) -> None:
        assert _extract_file_id("https://mxdrop.to/f/abc123") == "abc123"

    def test_m1xdrop(self) -> None:
        assert _extract_file_id("https://m1xdrop.co/f/abc123") == "abc123"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.mixdrop.ag/f/abc123") == "abc123"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://mixdrop.ag/f/abc123") == "abc123"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/f/abc123") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/download/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><video>Player</video></body></html>"
_OFFLINE_PAGE = '<html><body><img src="/imgs/illustration-notfound.png"></body></html>'


class TestMixdropResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert MixdropResolver(http_client=client).name == "mixdrop"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://mixdrop.ag/f/abc123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://mixdrop.ag/f/abc123"
        result = await MixdropResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://mixdrop.ag/f/abc123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await MixdropResolver(http_client=client).resolve(
            "https://mixdrop.ag/f/abc123"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await MixdropResolver(http_client=client).resolve(
            "https://mixdrop.ag/f/abc123"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        result = await MixdropResolver(http_client=client).resolve(
            "https://mixdrop.ag/f/abc123"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        result = await MixdropResolver(http_client=client).resolve(
            "https://example.com/f/abc123"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://mixdrop.ag/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await MixdropResolver(http_client=client).resolve(
            "https://mixdrop.ag/f/abc123"
        )
        assert result is None
