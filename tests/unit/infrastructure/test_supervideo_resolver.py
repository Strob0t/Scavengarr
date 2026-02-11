"""Tests for SuperVideoResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.supervideo import (
    SuperVideoResolver,
    _extract_html5_video,
    _extract_jwplayer_source,
)


class TestExtractJwplayerSource:
    def test_standard_sources_array(self) -> None:
        html = """
        jwplayer("player").setup({
            sources: [{file:"https://cdn.supervideo.cc/v/abc123.mp4"}]
        });
        """
        assert (
            _extract_jwplayer_source(html) == "https://cdn.supervideo.cc/v/abc123.mp4"
        )

    def test_sources_with_label(self) -> None:
        html = """
        sources:[{file:"https://cdn.supervideo.cc/v/abc.mp4",label:"720p"}]
        """
        assert _extract_jwplayer_source(html) == "https://cdn.supervideo.cc/v/abc.mp4"

    def test_file_property_mp4(self) -> None:
        html = """var file = "https://cdn.example.com/video.mp4";"""
        assert _extract_jwplayer_source(html) == "https://cdn.example.com/video.mp4"

    def test_file_property_m3u8(self) -> None:
        html = """source: "https://cdn.example.com/master.m3u8" """
        assert _extract_jwplayer_source(html) == "https://cdn.example.com/master.m3u8"

    def test_no_match(self) -> None:
        assert _extract_jwplayer_source("<html></html>") is None


class TestExtractHtml5Video:
    def test_source_tag(self) -> None:
        html = """<video><source src="https://cdn.example.com/v.mp4"></video>"""
        assert _extract_html5_video(html) == "https://cdn.example.com/v.mp4"

    def test_video_src(self) -> None:
        html = """<video src="https://cdn.example.com/v.mp4"></video>"""
        assert _extract_html5_video(html) == "https://cdn.example.com/v.mp4"

    def test_no_match(self) -> None:
        assert _extract_html5_video("<html></html>") is None


class TestSuperVideoResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = SuperVideoResolver(http_client=client)
        assert resolver.name == "supervideo"

    @pytest.mark.asyncio
    async def test_extracts_jwplayer_source(self) -> None:
        html = """
        <html><script>
        jwplayer("vplayer").setup({
            sources: [{file:"https://sv1.supervideo.cc/v/abc123.mp4"}],
            image: "/thumb.jpg"
        });
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://sv1.supervideo.cc/v/abc123.mp4"
        assert result.is_hls is False

    @pytest.mark.asyncio
    async def test_extracts_hls_source(self) -> None:
        html = """
        <html><script>
        sources: [{file:"https://sv1.supervideo.cc/hls/abc/master.m3u8"}]
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is not None
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_extracts_html5_video_fallback(self) -> None:
        html = """
        <html><video>
        <source src="https://sv1.supervideo.cc/v/abc.mp4" type="video/mp4">
        </video></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://sv1.supervideo.cc/v/abc.mp4"

    @pytest.mark.asyncio
    async def test_returns_none_when_offline(self) -> None:
        html = """<html><div class="fake-signup">Sign up</div></html>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_normalizes_non_embed_url(self) -> None:
        html = """
        sources: [{file:"https://sv1.supervideo.cc/v/abc.mp4"}]
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/abc123def456")

        assert result is not None
        # Verify the URL was normalized to /e/ format
        call_url = client.get.call_args[0][0]
        assert "/e/" in call_url

    @pytest.mark.asyncio
    async def test_returns_none_when_no_source_found(self) -> None:
        html = "<html><body><h1>Player</h1></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")
        assert result is None
