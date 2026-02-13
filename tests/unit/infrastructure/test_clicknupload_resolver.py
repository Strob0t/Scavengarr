"""Tests for ClicknuploadResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.clicknupload import (
    ClicknuploadResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_click_domain(self) -> None:
        url = "https://clicknupload.click/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_to_domain(self) -> None:
        url = "https://clicknupload.to/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_clickndownload(self) -> None:
        url = "https://clickndownload.cc/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_with_filename(self) -> None:
        url = "https://clicknupload.click/abc123def456/movie.mkv.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        url = "https://www.clicknupload.click/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_http_scheme(self) -> None:
        url = "http://clicknupload.click/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://clicknupload.click/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>ClicknUpload</title></head>
<body>
<h4>Movie.2025.1080p.mkv</h4>
<span>4.0 GB</span>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<h1>File Not Found</h1>
</body></html>
"""


class TestClicknuploadResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = ClicknuploadResolver(http_client=client)
        assert resolver.name == "clicknupload"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://clicknupload.click/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = ClicknuploadResolver(http_client=client)
        url = "https://clicknupload.click/abc123def456"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://clicknupload.click/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = ClicknuploadResolver(http_client=client)
        result = await resolver.resolve("https://clicknupload.click/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = ClicknuploadResolver(http_client=client)
        result = await resolver.resolve("https://clicknupload.click/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = ClicknuploadResolver(http_client=client)
        result = await resolver.resolve("https://clicknupload.click/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = ClicknuploadResolver(http_client=client)
        result = await resolver.resolve("https://example.com/abc123def456")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://clicknupload.click/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = ClicknuploadResolver(http_client=client)
        result = await resolver.resolve("https://clicknupload.click/abc123def456")
        assert result is None
