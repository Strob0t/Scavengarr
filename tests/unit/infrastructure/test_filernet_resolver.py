"""Tests for FilerNetResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.filernet import (
    FilerNetResolver,
    _extract_hash,
)

# ---------------------------------------------------------------------------
# Hash extraction
# ---------------------------------------------------------------------------


class TestExtractHash:
    def test_get_url(self) -> None:
        assert _extract_hash("https://filer.net/get/abc123def") == "abc123def"

    def test_dl_url(self) -> None:
        assert _extract_hash("https://filer.net/dl/xyz789") == "xyz789"

    def test_www_prefix(self) -> None:
        assert _extract_hash("https://www.filer.net/get/abc123") == "abc123"

    def test_http_scheme(self) -> None:
        assert _extract_hash("http://filer.net/get/abc123") == "abc123"

    def test_app_php_prefix(self) -> None:
        """JD2 supports app.php prefix — our regex matches it too."""
        assert _extract_hash("https://filer.net/app.php/get/abc123") == "abc123"

    def test_non_filer_domain(self) -> None:
        assert _extract_hash("https://example.com/get/abc123") is None

    def test_no_path_match(self) -> None:
        assert _extract_hash("https://filer.net/folder/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_hash("") is None

    def test_invalid_url(self) -> None:
        assert _extract_hash("not-a-url") is None


# ---------------------------------------------------------------------------
# Status API response fixture
# ---------------------------------------------------------------------------
_STATUS_RESPONSE = {
    "code": 200,
    "status": "success",
    "data": {
        "file_hash": "abc123def",
        "file_name": "Movie.2025.German.DL.1080p.BluRay.x264.mkv",
        "file_size": 4294967296,
        "premium_only": False,
        "view_count": 42,
    },
}

_NOT_FOUND_RESPONSE = {
    "code": 505,
    "status": "file not found",
    "data": {},
}


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------


class TestFilerNetResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = FilerNetResolver(http_client=client)
        assert resolver.name == "filer"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _STATUS_RESPONSE

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")

        assert result is not None
        assert result.video_url == "https://filer.net/get/abc123def"

    @pytest.mark.asyncio
    async def test_calls_status_api(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _STATUS_RESPONSE

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        await resolver.resolve("https://filer.net/get/abc123def")

        client.get.assert_called_once_with(
            "https://filer.net/api/status/abc123def.json",
            timeout=15,
        )

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _NOT_FOUND_RESPONSE

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("bad json")

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://example.com/get/abc123")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_hash_mismatch(self) -> None:
        """If API returns a different hash, treat as not found."""
        response = {
            "code": 200,
            "status": "success",
            "data": {
                "file_hash": "differenthash",
                "file_name": "file.mkv",
                "file_size": 1000,
            },
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")
        assert result is None

    @pytest.mark.asyncio
    async def test_dl_url_pattern(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 200,
            "status": "success",
            "data": {
                "file_hash": "xyz789",
                "file_name": "file.mkv",
                "file_size": 500,
            },
        }

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/dl/xyz789")

        assert result is not None
        assert result.video_url == "https://filer.net/get/xyz789"

    @pytest.mark.asyncio
    async def test_returns_none_when_data_not_dict(self) -> None:
        response = {
            "code": 200,
            "status": "success",
            "data": [],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilerNetResolver(http_client=client)
        result = await resolver.resolve("https://filer.net/get/abc123def")
        assert result is None
