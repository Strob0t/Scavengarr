"""Tests for AlphaddlResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.alphaddl import (
    AlphaddlResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert (
            _extract_file_id("https://alphaddl.com/movie-2025-1080p")
            == "movie-2025-1080p"
        )

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.alphaddl.com/movie-2025") == "movie-2025"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://alphaddl.com/movie-2025") == "movie-2025"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/movie-2025") is None

    def test_short_slug_rejected(self) -> None:
        assert _extract_file_id("https://alphaddl.com/ab") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><h1>Movie Download</h1></body></html>"
_OFFLINE_PAGE = "<html><body>Page not found</body></html>"


class TestAlphaddlResolver:
    def test_name(self) -> None:
        assert AlphaddlResolver(http_client=httpx.AsyncClient()).name == "alphaddl"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://alphaddl.com/movie-2025-1080p"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await AlphaddlResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://alphaddl.com/movie-2025-1080p"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await AlphaddlResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://alphaddl.com/movie-2025-1080p"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await AlphaddlResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://alphaddl.com/movie-2025-1080p"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await AlphaddlResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await AlphaddlResolver(http_client=client).resolve(
                "https://example.com/movie-2025-1080p"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://alphaddl.com/movie-2025-1080p"
        error_url = "https://alphaddl.com/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await AlphaddlResolver(http_client=client).resolve(url)
        assert result is None
