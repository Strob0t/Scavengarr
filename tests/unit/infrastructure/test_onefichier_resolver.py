"""Tests for OnefichierResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.onefichier import (
    OnefichierResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_main_domain(self) -> None:
        url = "https://1fichier.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_alterupload(self) -> None:
        url = "https://alterupload.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_cjoint(self) -> None:
        url = "https://cjoint.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_www_prefix(self) -> None:
        url = "https://www.1fichier.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_http_scheme(self) -> None:
        url = "http://1fichier.com/?abc12345"
        assert _extract_file_id(url) == "abc12345"

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/?abc12345") is None

    def test_short_id_rejected(self) -> None:
        assert _extract_file_id("https://1fichier.com/?abc") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>1fichier.com: Cloud Storage</title></head>
<body>
<h2>Movie.2025.1080p.mkv</h2>
<span>4.0 GB</span>
<a class="ok btn-general btn-orange">Download</a>
</body>
</html>
"""

_OFFLINE_PAGE = """
<html><body>
<p>File not found</p>
<p>The requested file could not be found</p>
</body></html>
"""


class TestOnefichierResolver:
    def test_name(self) -> None:
        resolver = OnefichierResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "1fichier"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://1fichier.com/?abc12345"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            result = await OnefichierResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_offline(self) -> None:
        url = "https://1fichier.com/?abc12345"
        respx.get(url).respond(200, text=_OFFLINE_PAGE)

        async with httpx.AsyncClient() as client:
            result = await OnefichierResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://1fichier.com/?abc12345"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            result = await OnefichierResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://1fichier.com/?abc12345"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await OnefichierResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await OnefichierResolver(http_client=client).resolve(
                "https://example.com/?abc12345"
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://1fichier.com/?abc12345"
        error_url = "https://1fichier.com/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            result = await OnefichierResolver(http_client=client).resolve(url)
        assert result is None
