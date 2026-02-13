"""Tests for NitroflareResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.nitroflare import (
    NitroflareResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_main_domain(self) -> None:
        url = "https://nitroflare.com/view/ABCDEF123456"
        assert _extract_file_id(url) == "ABCDEF123456"

    def test_nitro_download(self) -> None:
        url = "https://nitro.download/view/ABCDEF123456"
        assert _extract_file_id(url) == "ABCDEF123456"

    def test_watch_path(self) -> None:
        url = "https://nitroflare.com/watch/ABCDEF123456"
        assert _extract_file_id(url) == "ABCDEF123456"

    def test_www_prefix(self) -> None:
        url = "https://www.nitroflare.com/view/ABCDEF123456"
        assert _extract_file_id(url) == "ABCDEF123456"

    def test_http_scheme(self) -> None:
        url = "http://nitroflare.com/view/ABCDEF123456"
        assert _extract_file_id(url) == "ABCDEF123456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/view/ABCDEF123456") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://nitroflare.com/download/ABCDEF123456") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>NitroFlare.com</title></head>
<body>
<h1>Movie.2025.1080p.mkv</h1>
<span>Size: 4.0 GB</span>
<button>Download</button>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<p>This file has been removed</p>
</body></html>
"""


class TestNitroflareResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = NitroflareResolver(http_client=client)
        assert resolver.name == "nitroflare"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://nitroflare.com/view/ABCDEF123456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = NitroflareResolver(http_client=client)
        url = "https://nitroflare.com/view/ABCDEF123456"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://nitroflare.com/view/ABCDEF123456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = NitroflareResolver(http_client=client)
        result = await resolver.resolve("https://nitroflare.com/view/ABCDEF123456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = NitroflareResolver(http_client=client)
        result = await resolver.resolve("https://nitroflare.com/view/ABCDEF123456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = NitroflareResolver(http_client=client)
        result = await resolver.resolve("https://nitroflare.com/view/ABCDEF123456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = NitroflareResolver(http_client=client)
        result = await resolver.resolve("https://example.com/view/ABCDEF123456")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://nitroflare.com/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = NitroflareResolver(http_client=client)
        result = await resolver.resolve("https://nitroflare.com/view/ABCDEF123456")
        assert result is None
