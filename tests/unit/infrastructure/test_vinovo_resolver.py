"""Tests for VinovoResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.vinovo import (
    VinovoResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_to_domain_with_e_path(self) -> None:
        url = "https://vinovo.to/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_to_domain_with_d_path(self) -> None:
        url = "https://vinovo.to/d/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_si_domain(self) -> None:
        url = "https://vinovo.si/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        url = "https://www.vinovo.to/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_http_scheme(self) -> None:
        url = "http://vinovo.to/d/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_longer_id(self) -> None:
        url = "https://vinovo.to/e/abc123def456789"
        assert _extract_file_id(url) == "abc123def456789"

    def test_non_vinovo_domain(self) -> None:
        assert _extract_file_id("https://example.com/e/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://vinovo.to/e/abc123") is None

    def test_no_path_prefix(self) -> None:
        # Vinovo requires /e/ or /d/ prefix
        assert _extract_file_id("https://vinovo.to/abc123def456") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>vinovo.to</title></head>
<body>
<div class="player-area">
<video src="https://cdn.vinovo.to/video.mp4"></video>
</div>
</body>
</html>
"""

_OFFLINE_NOT_FOUND = """
<html><body>
<h1>File Not Found</h1>
<p>The requested file could not be found.</p>
</body></html>
"""

_OFFLINE_REMOVED = """
<html><body>
<p>This file was removed by the administrator.</p>
</body></html>
"""


class TestVinovoResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = VinovoResolver(http_client=client)
        assert resolver.name == "vinovo"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://vinovo.to/e/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VinovoResolver(http_client=client)
        url = "https://vinovo.to/e/abc123def456"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_NOT_FOUND
        mock_resp.url = "https://vinovo.to/e/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VinovoResolver(http_client=client)
        result = await resolver.resolve("https://vinovo.to/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_removed(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_REMOVED
        mock_resp.url = "https://vinovo.to/e/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VinovoResolver(http_client=client)
        result = await resolver.resolve("https://vinovo.to/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VinovoResolver(http_client=client)
        result = await resolver.resolve("https://vinovo.to/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = VinovoResolver(http_client=client)
        result = await resolver.resolve("https://vinovo.to/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = VinovoResolver(http_client=client)
        result = await resolver.resolve("https://example.com/e/abc123def456")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://vinovo.to/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VinovoResolver(http_client=client)
        result = await resolver.resolve("https://vinovo.to/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_si_domain_resolves(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://vinovo.si/d/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VinovoResolver(http_client=client)
        url = "https://vinovo.si/d/abc123def456"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url
