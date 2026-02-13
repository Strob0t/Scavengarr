"""Tests for VidguardResolver."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.vidguard import (
    VidguardResolver,
    _extract_file_id,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_vidguard_to_embed(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_vidguard_to_download(self) -> None:
        url = "https://vidguard.to/d/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_vidguard_to_view(self) -> None:
        url = "https://vidguard.to/v/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_vgfplay_domain(self) -> None:
        url = "https://vgfplay.com/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_vembed_domain(self) -> None:
        url = "https://vembed.net/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_listeamed_domain(self) -> None:
        url = "https://listeamed.net/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_moflix_stream_domain(self) -> None:
        url = "https://moflix-stream.day/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_vid_guard_domain(self) -> None:
        url = "https://vid-guard.com/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_bembed_domain(self) -> None:
        url = "https://bembed.net/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_www_prefix(self) -> None:
        url = "https://www.vidguard.to/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_http_scheme(self) -> None:
        url = "http://vidguard.to/e/abc123XYZ"
        assert _extract_file_id(url) == "abc123XYZ"

    def test_long_id(self) -> None:
        url = "https://vidguard.to/e/abcdefghijklmnopqrstuvwxyz123456"
        assert _extract_file_id(url) == "abcdefghijklmnopqrstuvwxyz123456"

    def test_short_id_accepted(self) -> None:
        # Vidguard accepts variable-length IDs (not restricted to 12)
        url = "https://vidguard.to/e/abc"
        assert _extract_file_id(url) == "abc"

    def test_non_vidguard_domain(self) -> None:
        assert _extract_file_id("https://example.com/e/abc123XYZ") is None

    def test_no_path_prefix(self) -> None:
        assert _extract_file_id("https://vidguard.to/abc123XYZ") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_VALID_PAGE = """
<html>
<head><title>VidGuard - Video Player</title></head>
<body>
<div id="player">
<video src="https://cdn.vidguard.to/stream.m3u8"></video>
</div>
</body>
</html>
"""

_OFFLINE_NOT_FOUND = """
<html><body>
<h1>File Not Found</h1>
<p>err:1002</p>
</body></html>
"""

_OFFLINE_VIDEO_NOT_FOUND = """
<html><body>
<h1>Video not found</h1>
<p>The video you are looking for is not found.</p>
</body></html>
"""


class TestVidguardResolver:
    def test_name(self) -> None:
        resolver = VidguardResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "vidguard"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_file(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_not_found(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).respond(200, text=_OFFLINE_NOT_FOUND)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_video_not_found(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).respond(200, text=_OFFLINE_VIDEO_NOT_FOUND)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_404(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).respond(404)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_403(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).respond(403)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_http_error(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).respond(500)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        respx.get(url).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve("https://example.com/e/abc123XYZ")
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_error_redirect(self) -> None:
        url = "https://vidguard.to/e/abc123XYZ"
        error_url = "https://vidguard.to/404"
        respx.get(url).respond(302, headers={"Location": error_url})
        respx.get(error_url).respond(200, text="<html><body>Error</body></html>")

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_vgfplay_domain_resolves(self) -> None:
        url = "https://vgfplay.com/e/abc123XYZ"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_listeamed_domain_resolves(self) -> None:
        url = "https://listeamed.net/e/abc123XYZ"
        respx.get(url).respond(200, text=_VALID_PAGE)

        async with httpx.AsyncClient() as client:
            resolver = VidguardResolver(http_client=client)
            result = await resolver.resolve(url)

        assert result is not None
        assert result.video_url == url
