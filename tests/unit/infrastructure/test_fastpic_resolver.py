"""Tests for FastpicResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.fastpic import (
    FastpicResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_org_domain(self) -> None:
        url = "https://fastpic.org/view/123/2025/0101/abcdef01234567890abcdef012345678.jpg.html"
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.jpg"

    def test_ru_domain(self) -> None:
        url = (
            "https://fastpic.ru/fullview/123/2025/abcdef01234567890abcdef012345678.png"
        )
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.png"

    def test_www_prefix(self) -> None:
        url = "https://www.fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.jpg"

    def test_http_scheme(self) -> None:
        url = "http://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        assert _extract_file_id(url) == "abcdef01234567890abcdef012345678.jpg"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/view/123/abcdef.jpg") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://fastpic.org/upload/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><img src='image.jpg'></body></html>"
_OFFLINE_PAGE = "<html><body>404 Not Found</body></html>"


class TestFastpicResolver:
    def test_name(self) -> None:
        assert FastpicResolver(http_client=httpx.AsyncClient()).name == "fastpic"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await FastpicResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await FastpicResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await FastpicResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await FastpicResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await FastpicResolver(http_client=client).resolve(
                "https://example.com/view/123/abc.jpg"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://fastpic.org/view/123/abcdef01234567890abcdef012345678.jpg"
        error_url = "https://fastpic.org/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await FastpicResolver(http_client=client).resolve(url)
        assert result is None
