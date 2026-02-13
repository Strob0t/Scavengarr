"""Tests for FilecryptResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.filecrypt import (
    FilecryptResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_main_domain(self) -> None:
        assert (
            _extract_file_id("https://filecrypt.cc/Container/ABC123DEF") == "ABC123DEF"
        )

    def test_www_prefix(self) -> None:
        assert (
            _extract_file_id("https://www.filecrypt.cc/Container/ABC123DEF")
            == "ABC123DEF"
        )

    def test_http_scheme(self) -> None:
        assert (
            _extract_file_id("http://filecrypt.cc/Container/ABC123DEF") == "ABC123DEF"
        )

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/Container/ABC123DEF") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://filecrypt.cc/file/ABC123DEF") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><table>Container links</table></body></html>"
_OFFLINE_PAGE = "<html><body>Container not found</body></html>"


class TestFilecryptResolver:
    def test_name(self) -> None:
        assert FilecryptResolver(http_client=httpx.AsyncClient()).name == "filecrypt"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://filecrypt.cc/Container/ABC123DEF"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await FilecryptResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://filecrypt.cc/Container/ABC123DEF"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await FilecryptResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://filecrypt.cc/Container/ABC123DEF"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await FilecryptResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://filecrypt.cc/Container/ABC123DEF"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await FilecryptResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await FilecryptResolver(http_client=client).resolve(
                "https://example.com/Container/ABC123DEF"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://filecrypt.cc/Container/ABC123DEF"
        error_url = "https://filecrypt.cc/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await FilecryptResolver(http_client=client).resolve(url)
        assert result is None
