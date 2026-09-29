"""Tests for VixeoResolver (vixeo.io, Vidsonic's current player)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from scavengarr.infrastructure.browser.stealth_pool import CapturedMedia
from scavengarr.infrastructure.hoster_resolvers.vixeo import (
    VixeoResolver,
    _extract_file_id,
)

_URL = "https://vixeo.io/e/KD2Ztr3euxYK"
_HLS = (
    "https://sfy-01-fr.vidsonic.net/secure/98/KD2Ztr3euxYK/video.mp4/index.m3u8"
    "?server_id=3&expires=1790668836&file_id=KD2Ztr3euxYK&md5=x"
)


class TestExtractFileId:
    def test_embed_url(self) -> None:
        assert _extract_file_id(_URL) == "KD2Ztr3euxYK"

    @pytest.mark.parametrize(
        "url", ["https://vixeo.io/login", "https://example.com/e/KD2Ztr3euxYK", ""]
    )
    def test_not_a_video(self, url: str) -> None:
        assert _extract_file_id(url) is None


class TestVixeoResolver:
    def test_name(self) -> None:
        assert VixeoResolver(http_client=httpx.AsyncClient()).name == "vixeo"

    async def test_captures_player_stream(self) -> None:
        pool = AsyncMock()
        pool.capture_media = AsyncMock(return_value=CapturedMedia(_HLS, None))

        resolver = VixeoResolver(http_client=httpx.AsyncClient(), stealth_pool=pool)
        result = await resolver.resolve(_URL)

        assert result is not None
        assert result.video_url == _HLS
        assert result.is_hls is True
        assert pool.capture_media.await_args.args[0] == _URL

    async def test_login_page_is_not_resolved(self) -> None:
        pool = AsyncMock()

        resolver = VixeoResolver(http_client=httpx.AsyncClient(), stealth_pool=pool)

        assert await resolver.resolve("https://vixeo.io/login") is None
        pool.capture_media.assert_not_awaited()

    async def test_without_browser_returns_none(self) -> None:
        resolver = VixeoResolver(http_client=httpx.AsyncClient())

        assert await resolver.resolve(_URL) is None
