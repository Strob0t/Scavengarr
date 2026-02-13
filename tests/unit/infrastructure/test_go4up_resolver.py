"""Tests for Go4upResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.go4up import (
    Go4upResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_dl_path(self) -> None:
        assert _extract_file_id("https://go4up.com/dl/abc123def") == "abc123def"

    def test_link_path(self) -> None:
        assert _extract_file_id("https://go4up.com/link/abc123def") == "abc123def"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.go4up.com/dl/abc123def") == "abc123def"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://go4up.com/dl/abc123def") == "abc123def"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/dl/abc123def") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://go4up.com/file/abc123def") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><h1>Download Links</h1></body></html>"
_OFFLINE_PAGE = "<html><body><p>Link not found</p></body></html>"


class TestGo4upResolver:
    def test_name(self) -> None:
        assert Go4upResolver(http_client=httpx.AsyncClient()).name == "go4up"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://go4up.com/dl/abc123def"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await Go4upResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://go4up.com/dl/abc123def"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await Go4upResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://go4up.com/dl/abc123def"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await Go4upResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://go4up.com/dl/abc123def"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await Go4upResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await Go4upResolver(http_client=client).resolve(
                "https://example.com/dl/abc123def"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://go4up.com/dl/abc123def"
        error_url = "https://go4up.com/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await Go4upResolver(http_client=client).resolve(url)
        assert result is None
