"""Tests for UploadedResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.uploaded import (
    UploadedResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_uploaded_net(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_uploaded_to(self) -> None:
        url = "https://uploaded.to/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_ul_to(self) -> None:
        url = "https://ul.to/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_www_prefix(self) -> None:
        url = "https://www.uploaded.net/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_http_scheme(self) -> None:
        url = "http://uploaded.net/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/file/abc123def") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>uploaded.net</title></head>
<body>
<h1>Movie.2025.1080p.mkv</h1>
<span>Size: 4.0 GB</span>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<h1>File Not Found</h1>
</body></html>
"""


class TestUploadedResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = UploadedResolver(http_client=client)
        assert resolver.name == "uploaded"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://uploaded.net/file/abc123def"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = UploadedResolver(http_client=client)
        url = "https://uploaded.net/file/abc123def"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://uploaded.net/file/abc123def"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = UploadedResolver(http_client=client)
        result = await resolver.resolve("https://uploaded.net/file/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = UploadedResolver(http_client=client)
        result = await resolver.resolve("https://uploaded.net/file/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = UploadedResolver(http_client=client)
        result = await resolver.resolve("https://uploaded.net/file/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = UploadedResolver(http_client=client)
        result = await resolver.resolve("https://example.com/file/abc123def")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://uploaded.net/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = UploadedResolver(http_client=client)
        result = await resolver.resolve("https://uploaded.net/file/abc123def")
        assert result is None
