"""Tests for MediafireResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.mediafire import (
    MediafireResolver,
    _extract_file_id,
)

_API_URL = "https://www.mediafire.com/api/1.5/file/get_info.php"


class TestExtractFileId:
    def test_file_path(self) -> None:
        url = "https://www.mediafire.com/file/abc123def/movie.mkv/file"
        assert _extract_file_id(url) == "abc123def"

    def test_file_path_no_filename(self) -> None:
        assert (
            _extract_file_id("https://www.mediafire.com/file/abc123def") == "abc123def"
        )

    def test_download_path(self) -> None:
        assert _extract_file_id("https://www.mediafire.com/download/abc123") == "abc123"

    def test_view_path(self) -> None:
        assert _extract_file_id("https://www.mediafire.com/view/abc123") == "abc123"

    def test_query_based(self) -> None:
        assert _extract_file_id("https://www.mediafire.com/?abc123") == "abc123"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://mediafire.com/file/abc123") == "abc123"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/file/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None

    def test_no_file_id(self) -> None:
        assert _extract_file_id("https://www.mediafire.com/") is None


_SUCCESS_RESPONSE = {
    "response": {
        "result": "Success",
        "file_info": {
            "quickkey": "abc123",
            "filename": "movie.mkv",
            "size": "1024000",
            "hash": "abcdef1234567890",
        },
    },
}

_DELETED_RESPONSE = {
    "response": {
        "result": "Success",
        "file_info": {
            "quickkey": "abc123",
            "filename": "movie.mkv",
            "delete_date": "2025-01-15 10:30:00",
        },
    },
}

_ERROR_110_RESPONSE = {
    "response": {
        "result": "Error",
        "error": 110,
        "message": "Unknown or invalid quickkey.",
    },
}


class TestMediafireResolver:
    def test_name(self) -> None:
        resolver = MediafireResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "mediafire"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://www.mediafire.com/file/abc123/movie.mkv/file"
        respx.get(_API_URL).respond(200, json=_SUCCESS_RESPONSE)

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == "https://www.mediafire.com/file/abc123"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline_error_110(self) -> None:
        url = "https://www.mediafire.com/file/abc123"
        respx.get(_API_URL).respond(200, json=_ERROR_110_RESPONSE)

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_deleted_file(self) -> None:
        url = "https://www.mediafire.com/file/abc123"
        respx.get(_API_URL).respond(200, json=_DELETED_RESPONSE)

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://www.mediafire.com/file/abc123"
        respx.get(_API_URL).respond(500)

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://www.mediafire.com/file/abc123"
        respx.get(_API_URL).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(
                "https://example.com/file/abc123",
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_invalid_json(self) -> None:
        url = "https://www.mediafire.com/file/abc123"
        respx.get(_API_URL).respond(200, text="not-json")

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_missing_file_info(self) -> None:
        url = "https://www.mediafire.com/file/abc123"
        respx.get(_API_URL).respond(
            200,
            json={"response": {"result": "Success", "file_info": "invalid"}},
        )

        async with httpx.AsyncClient() as client:
            result = await MediafireResolver(http_client=client).resolve(url)
        assert result is None
