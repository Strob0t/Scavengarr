"""Tests for VidmolyResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.vidmoly import (
    VidmolyResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_me_domain(self) -> None:
        url = "https://vidmoly.me/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_biz_domain(self) -> None:
        url = "https://vidmoly.biz/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_net_domain(self) -> None:
        url = "https://vidmoly.net/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_www_prefix(self) -> None:
        url = "https://www.vidmoly.me/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_http_scheme(self) -> None:
        url = "http://vidmoly.me/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_w_path(self) -> None:
        url = "https://vidmoly.me/w/abc123def456"
        assert _extract_file_id(url) == "abc123def456"

    def test_embed_path(self) -> None:
        url = "https://vidmoly.me/embed-abc123def456.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_with_filename_html(self) -> None:
        url = "https://vidmoly.me/abc123def456/movie.mkv.html"
        assert _extract_file_id(url) == "abc123def456"

    def test_non_vidmoly_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def456") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://vidmoly.me/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>vidmoly.me - abc123def456</title></head>
<body>
<h4>Movie.2025.German.DL.1080p.BluRay.x264.mkv</h4>
<div id="player">
<script>var player = new Clappr.Player(...);</script>
</div>
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
<h1>Error</h1>
<p>This file was removed by the administrator.</p>
</body></html>
"""

_OFFLINE_NOT_FOUND = """
<html><body>
<h1>File Not Found</h1>
<p>The file you are looking for does not exist.</p>
</body></html>
"""

_OFFLINE_NOTICE = """
<html><body>
<script>window.location="/notice.php";</script>
</body></html>
"""


class TestVidmolyResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = VidmolyResolver(http_client=client)
        assert resolver.name == "vidmoly"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://vidmoly.me/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        url = "https://vidmoly.me/abc123def456"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_expired(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_EXPIRED
        mock_resp.url = "https://vidmoly.me/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_removed(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_REMOVED
        mock_resp.url = "https://vidmoly.me/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_not_found(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_NOT_FOUND
        mock_resp.url = "https://vidmoly.me/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_notice(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_NOTICE
        mock_resp.url = "https://vidmoly.me/abc123def456"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://example.com/abc123def456")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://vidmoly.me/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VidmolyResolver(http_client=client)
        result = await resolver.resolve("https://vidmoly.me/abc123def456")
        assert result is None
