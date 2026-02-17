"""Tests for VidkingResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.vidking import (
    VidkingResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert _extract_file_id("https://vidking.net/abc123def") == "abc123def"

    def test_e_prefix(self) -> None:
        assert _extract_file_id("https://vidking.net/e/abc123def") == "abc123def"

    def test_d_prefix(self) -> None:
        assert _extract_file_id("https://vidking.net/d/abc123def") == "abc123def"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.vidking.net/abc123def") == "abc123def"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://vidking.net/abc123def") == "abc123def"

    def test_embed_movie_prefix(self) -> None:
        assert _extract_file_id("https://vidking.net/embed/movie/563") == "563"

    def test_embed_movie_long_id(self) -> None:
        assert _extract_file_id("https://vidking.net/embed/movie/abc123") == "abc123"

    def test_embed_tv_prefix(self) -> None:
        assert _extract_file_id("https://vidking.net/embed/tv/1396/1/1") == "1396"

    def test_embed_tv_www(self) -> None:
        assert (
            _extract_file_id("https://www.vidking.net/embed/tv/93405/2/10") == "93405"
        )

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/abc123def") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><video>Player</video></body></html>"
_OFFLINE_PAGE = "<html><body>Video not found</body></html>"


class TestVidkingResolver:
    def test_name(self) -> None:
        assert VidkingResolver(http_client=httpx.AsyncClient()).name == "vidking"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://vidking.net/abc123def"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_embed_movie_url(self) -> None:
        url = "https://vidking.net/embed/movie/563"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_embed_tv_url(self) -> None:
        url = "https://www.vidking.net/embed/tv/1396/1/1"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://vidking.net/abc123def"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://vidking.net/abc123def"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://vidking.net/abc123def"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(
                "https://example.com/abc123def"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://vidking.net/abc123def"
        error_url = "https://vidking.net/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await VidkingResolver(http_client=client).resolve(url)
        assert result is None
