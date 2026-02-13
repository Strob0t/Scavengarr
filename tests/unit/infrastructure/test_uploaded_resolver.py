"""Tests for UploadedResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.uploaded import (
    UploadedResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_uploaded_net(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_uploaded_to(self) -> None:
        url = "https://uploaded.to/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_ul_to(self) -> None:
        url = "https://ul.to/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_www_prefix(self) -> None:
        url = "https://www.uploaded.net/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_http_scheme(self) -> None:
        url = "http://uploaded.net/file/abc123def"
        assert _extract_file_id(url) == "abc123def"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/file/abc123def") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>uploaded.net</title></head>
<body>
<h1>Movie.2025.1080p.mkv</h1>
<span>Size: 4.0 GB</span>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<h1>File Not Found</h1>
</body></html>
"""


class TestUploadedResolver:
    def test_name(self) -> None:
        resolver = UploadedResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "uploaded"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            resolver = UploadedResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            resolver = UploadedResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            resolver = UploadedResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            resolver = UploadedResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = UploadedResolver(http_client=client)
            result = await resolver.resolve("https://example.com/file/abc123def")
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://uploaded.net/file/abc123def"
        error_url = "https://uploaded.net/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            resolver = UploadedResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None
