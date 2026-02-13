"""Tests for OnefichierResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.onefichier import (
    OnefichierResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_main_domain(self) -> None:
        url = "https://1fichier.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_alterupload(self) -> None:
        url = "https://alterupload.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_cjoint(self) -> None:
        url = "https://cjoint.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_www_prefix(self) -> None:
        url = "https://www.1fichier.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_http_scheme(self) -> None:
        url = "http://1fichier.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/?abc12345") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://1fichier.com/?abc") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>1fichier.com: Cloud Storage</title></head>
<body>
<h2>Movie.2025.1080p.mkv</h2>
<span>4.0 GB</span>
<a class="ok btn-general btn-orange">Download</a>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<p>File not found</p>
<p>The requested file could not be found</p>
</body></html>
"""


class TestOnefichierResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = OnefichierResolver(http_client=client)
        assert resolver.name == "1fichier"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://1fichier.com/?abc12345"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = OnefichierResolver(http_client=client)
        url = "https://1fichier.com/?abc12345"
        result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://1fichier.com/?abc12345"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = OnefichierResolver(http_client=client)
        result = await resolver.resolve("https://1fichier.com/?abc12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = OnefichierResolver(http_client=client)
        result = await resolver.resolve("https://1fichier.com/?abc12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = OnefichierResolver(http_client=client)
        result = await resolver.resolve("https://1fichier.com/?abc12345")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = OnefichierResolver(http_client=client)
        result = await resolver.resolve("https://example.com/?abc12345")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://1fichier.com/404"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = OnefichierResolver(http_client=client)
        result = await resolver.resolve("https://1fichier.com/?abc12345")
        assert result is None
