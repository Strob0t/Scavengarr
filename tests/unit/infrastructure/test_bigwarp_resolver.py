"""Tests for BigwarpResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.bigwarp import (
    BigwarpResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_io_domain(self) -> None:
        url = "https://bigwarp.io/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_art_domain(self) -> None:
        url = "https://bigwarp.art/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_cc_domain(self) -> None:
        url = "https://bigwarp.cc/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_pro_domain(self) -> None:
        url = "https://bigwarp.pro/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        url = "https://www.bigwarp.io/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_http_scheme(self) -> None:
        url = "http://bigwarp.io/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_e_path(self) -> None:
        url = "https://bigwarp.io/e/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_with_filename(self) -> None:
        url = "https://bigwarp.io/abc123def456/movie.mkv.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_non_bigwarp_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://bigwarp.io/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>bigwarp.io - abc123def456</title></head>
<body>
<h4>Movie.2025.German.DL.1080p.BluRay.x264.mkv</h4>
<span>Size: 4.0 GB</span>
<form method="POST">
<input type="hidden" name="op" value="download1">
<input type="submit" name="method_free" value="Start Download">
</form>
</body>
</html>
"""

_OFFLINE_EXPIRED = """
<html><body>
<h1>File Not Found</h1>
<p>The file expired</p>
</body></html>
"""

_OFFLINE_REMOVED = """
<html><body>
<p>The file was removed by admin</p>
</body></html>
"""

_OFFLINE_NOT_FOUND = """
<html><body>
<h1>File Not Found</h1>
</body></html>
"""

_OFFLINE_NO_LONGER = """
<html><body>
<div>File is no longer available</div>
</body></html>
"""


class TestBigwarpResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = BigwarpResolver(http_client=client)
        assert resolver.name == "bigwarp"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://bigwarp.io/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        url = "https://bigwarp.io/abc123def456"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_expired(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_EXPIRED
        mock_resp.url = "https://bigwarp.io/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_removed(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_REMOVED
        mock_resp.url = "https://bigwarp.io/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_NOT_FOUND
        mock_resp.url = "https://bigwarp.io/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_no_longer_available(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_NO_LONGER
        mock_resp.url = "https://bigwarp.io/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://example.com/abc123def456")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://bigwarp.io/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = BigwarpResolver(http_client=client)
        result = await resolver.resolve("https://bigwarp.io/abc123def456")
        assert result is None
