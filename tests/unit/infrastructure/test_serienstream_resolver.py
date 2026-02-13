"""Tests for SerienstreamResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.serienstream import (
    SerienstreamResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_s_to(self) -> None:
        assert (
            _extract_file_id("https://s.to/serie/stream/breaking-bad")
            == "stream/breaking-bad"
        )

    def test_serienstream_to(self) -> None:
        url = "https://serienstream.to/serie/stream/breaking-bad"
        assert _extract_file_id(url) == "stream/breaking-bad"

    def test_serien_sx(self) -> None:
        url = "https://serien.sx/serie/stream/breaking-bad"
        assert _extract_file_id(url) == "stream/breaking-bad"

    def test_www_s_to(self) -> None:
        assert (
            _extract_file_id("https://www.s.to/serie/stream/breaking-bad")
            == "stream/breaking-bad"
        )

    def test_http_scheme(self) -> None:
        assert (
            _extract_file_id("http://s.to/serie/stream/breaking-bad")
            == "stream/breaking-bad"
        )

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/serie/stream/breaking-bad") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://s.to/movie/breaking-bad") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><h1>Breaking Bad</h1><div>Streams</div></body></html>"
_OFFLINE_PAGE = "<html><body>Seite nicht gefunden</body></html>"


class TestSerienstreamResolver:
    def test_name(self) -> None:
        resolver = SerienstreamResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "serienstream"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://s.to/serie/stream/breaking-bad"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await SerienstreamResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://s.to/serie/stream/breaking-bad"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await SerienstreamResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://s.to/serie/stream/breaking-bad"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await SerienstreamResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://s.to/serie/stream/breaking-bad"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await SerienstreamResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await SerienstreamResolver(http_client=client).resolve(
                "https://example.com/serie/stream/breaking-bad"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://s.to/serie/stream/breaking-bad"
        error_url = "https://s.to/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await SerienstreamResolver(http_client=client).resolve(url)
        assert result is None
