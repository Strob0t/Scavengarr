"""Tests for MixdropResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.mixdrop import (
    MixdropResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain_f(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/f/abc123") == "abc123"

    def test_main_domain_e(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/e/abc123") == "abc123"

    def test_main_domain_emb(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/emb/abc123") == "abc123"

    def test_mxdrop(self) -> None:
        assert _extract_file_id("https://mxdrop.to/f/abc123") == "abc123"

    def test_m1xdrop(self) -> None:
        assert _extract_file_id("https://m1xdrop.co/f/abc123") == "abc123"

    def test_www_prefix(self) -> None:
        assert _extract_file_id("https://www.mixdrop.ag/f/abc123") == "abc123"

    def test_http_scheme(self) -> None:
        assert _extract_file_id("http://mixdrop.ag/f/abc123") == "abc123"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/f/abc123") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://mixdrop.ag/download/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><video>Player</video></body></html>"
_OFFLINE_PAGE = '<html><body><img src="/imgs/illustration-notfound.png"></body></html>'


class TestMixdropResolver:
    def test_name(self) -> None:
        resolver = MixdropResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "mixdrop"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://mixdrop.ag/f/abc123"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://mixdrop.ag/f/abc123"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://mixdrop.ag/f/abc123"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://mixdrop.ag/f/abc123"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(
                "https://example.com/f/abc123"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://mixdrop.ag/f/abc123"
        error_url = "https://mixdrop.ag/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(url)
        assert result is None
