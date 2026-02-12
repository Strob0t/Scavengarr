"""Tests for DDownloadResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.ddownload import (
    DDownloadResolver,
    _extract_file_id,
    _extract_filename,
    _extract_filesize,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_ddownload_com(self) -> None:
        assert _extract_file_id("https://ddownload.com/abc123def456") == "abc123def456"

    def test_ddl_to(self) -> None:
        assert _extract_file_id("https://ddl.to/abc123def456") == "abc123def456"

    def test_with_filename(self) -> None:
        url = "https://ddownload.com/abc123def456/movie.mkv.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.ddownload.com/abc123def456") == "abc123def456"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://ddownload.com/abc123def456") == "abc123def456"

    def test_non_ddownload_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://ddownload.com/abc123") is None

    def test_long_id_rejected(self) -> None:
        assert _extract_file_id("https://ddownload.com/abc123def456789") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None

    def test_subdomain_ignored(self) -> None:
        """Dead subdomains like api.ddl.to should still extract if domain matches."""
        assert _extract_file_id("https://api.ddl.to/abc123def456") is None


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------


class TestExtractFilename:
    def test_extracts_filename(self) -> None:
        html = '<h1 class="file-info-name">Movie.2025.1080p.mkv</h1>'
        assert _extract_filename(html) == "Movie.2025.1080p.mkv"

    def test_strips_whitespace(self) -> None:
        html = '<h1 class="file-info-name">  Movie.mkv  </h1>'
        assert _extract_filename(html) == "Movie.mkv"

    def test_no_match(self) -> None:
        assert _extract_filename("<html><body>Nothing</body></html>") == ""


class TestExtractFilesize:
    def test_span_filesize(self) -> None:
        html = '<span class="file-size">4.0 GB</span>'
        assert _extract_filesize(html) == "4.0 GB"

    def test_font_fallback(self) -> None:
        html = '[<font style="color:red">1481 MB</font>]'
        assert _extract_filesize(html) == "1481 MB"

    def test_span_preferred_over_font(self) -> None:
        html = (
            '<span class="file-size">4.0 GB</span>'
            '[<font style="color:red">4096 MB</font>]'
        )
        assert _extract_filesize(html) == "4.0 GB"

    def test_no_match(self) -> None:
        assert _extract_filesize("<html><body>Nothing</body></html>") == ""


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>ddownload.com - abc123def456</title></head>
<body>
<h1 class="file-info-name">Movie.2025.German.DL.1080p.BluRay.x264.mkv</h1>
<span class="file-size">4.0 GB</span>
<form method="POST">
<input type="hidden" name="op" value="download1">
<input type="submit" name="method_free" value="Free Download">
</form>
</body>
</html>
"""

_OFFLINE_NOT_FOUND = """
<html><body>
<h2>File Not Found</h2>
<p>The file you were looking for could not be found.</p>
</body></html>
"""

_OFFLINE_REMOVED = """
<html><body>
<p>This file was removed by the administrator.</p>
</body></html>
"""

_OFFLINE_COPYRIGHT = """
<html><body>
<p>This file was banned by copyright holder.</p>
</body></html>
"""

_OFFLINE_EXPIRED = """
<html><body>
<p>>The file expired</p>
</body></html>
"""

_OFFLINE_DELETED = """
<html><body>
<p>>The file was deleted by its owner</p>
</body></html>
"""

_MAINTENANCE_PAGE = """
<html><body>
<p>This server is in maintenance mode. Please try again later.</p>
</body></html>
"""


class TestDDownloadResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = DDownloadResolver(http_client=client)
        assert resolver.name == "ddownload"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")

        assert result is not None
        assert result.video_url == "https://ddownload.com/abc123def456"

    @pytest.mark.asyncio
    async def test_ddl_to_resolves_with_canonical_url(self) -> None:
        """ddl.to URLs should be canonicalized to ddownload.com."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddl.to/abc123def456")

        assert result is not None
        assert result.video_url == "https://ddownload.com/abc123def456"
        # Verify the canonical URL was used for the request.
        client.get.assert_called_once()
        call_url = client.get.call_args[0][0]
        assert call_url == "https://ddownload.com/abc123def456"

    @pytest.mark.asyncio
    async def test_returns_none_for_file_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_NOT_FOUND
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_removed_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_REMOVED
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_copyright_ban(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_COPYRIGHT
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_expired_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_EXPIRED
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_deleted_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_DELETED
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_maintenance(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _MAINTENANCE_PAGE
        mock_resp.url = "https://ddownload.com/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://example.com/abc123def456")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://ddownload.com/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DDownloadResolver(http_client=client)
        result = await resolver.resolve("https://ddownload.com/abc123def456")
        assert result is None
