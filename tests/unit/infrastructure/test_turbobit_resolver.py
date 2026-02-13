"""Tests for TurbobitResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.turbobit import (
    TurbobitResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_turbobit_net(self) -> None:
        url = "https://turbobit.net/abc123def456.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_turb_to(self) -> None:
        url = "https://turb.to/abc123def456.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_turbo_to(self) -> None:
        url = "https://turbo.to/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_download_free_path(self) -> None:
        url = "https://turbobit.net/download/free/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        url = "https://www.turbobit.net/abc123def456.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_http_scheme(self) -> None:
        url = "http://turbobit.net/abc123def456.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456.html") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://turbobit.net/abc12") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>TurboBit.net - Download</title></head>
<body>
<h1>Movie.2025.German.DL.1080p.mkv</h1>
<span class="file-size">4.0 GB</span>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<h1>File Not Found</h1>
<p>The file you requested was not found.</p>
</body></html>
"""


class TestTurbobitResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = TurbobitResolver(http_client=client)
        assert resolver.name == "turbobit"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://turbobit.net/abc123def456.html"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = TurbobitResolver(http_client=client)
        url = "https://turbobit.net/abc123def456.html"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://turbobit.net/abc123def456.html"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = TurbobitResolver(http_client=client)
        result = await resolver.resolve("https://turbobit.net/abc123def456.html")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = TurbobitResolver(http_client=client)
        result = await resolver.resolve("https://turbobit.net/abc123def456.html")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = TurbobitResolver(http_client=client)
        result = await resolver.resolve("https://turbobit.net/abc123def456.html")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = TurbobitResolver(http_client=client)
        result = await resolver.resolve("https://example.com/abc123def456.html")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://turbobit.net/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = TurbobitResolver(http_client=client)
        result = await resolver.resolve("https://turbobit.net/abc123def456.html")
        assert result is None
