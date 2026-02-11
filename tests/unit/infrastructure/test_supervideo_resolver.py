"""Tests for SuperVideoResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.supervideo import (
    SuperVideoResolver,
    _extract_html5_video,
    _extract_jwplayer_source,
    _is_cloudflare_block,
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


class TestIsCloudflareBlock:
    def test_403_with_just_a_moment(self) -> None:
        assert _is_cloudflare_block(403, "<title>Just a moment...</title>") is True

    def test_503_with_challenge_platform(self) -> None:
        assert _is_cloudflare_block(503, '<div id="challenge-platform">') is True

    def test_403_without_cloudflare_markers(self) -> None:
        assert _is_cloudflare_block(403, "<html>Forbidden</html>") is False

    def test_200_with_just_a_moment(self) -> None:
        assert _is_cloudflare_block(200, "Just a moment") is False

    def test_404_plain(self) -> None:
        assert _is_cloudflare_block(404, "Not found") is False


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
        mock_resp.text = "Not found"

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
    async def test_sends_complete_browser_headers(self) -> None:
        html = """sources: [{file:"https://sv1.supervideo.cc/v/abc.mp4"}]"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = SuperVideoResolver(http_client=client)
        await resolver.resolve("https://supervideo.cc/e/abc123def456")

        _, kwargs = client.get.call_args
        headers = kwargs["headers"]
        # Complete UA with Chrome and Safari tokens
        assert "Chrome/" in headers["User-Agent"]
        assert "Safari/" in headers["User-Agent"]
        # Accept and Accept-Language for Cloudflare bypass
        assert "Accept" in headers
        assert "text/html" in headers["Accept"]
        assert "Accept-Language" in headers
        assert "en-US" in headers["Accept-Language"]
        # Referer matches the embed URL
        assert "Referer" in headers

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


class TestSuperVideoPlaywrightFallback:
    """Tests for Cloudflare detection and Playwright fallback."""

    @pytest.mark.asyncio
    async def test_cloudflare_403_triggers_playwright_fallback(self) -> None:
        """httpx 403 + 'Just a moment' triggers Playwright fallback."""
        # httpx returns Cloudflare block
        cf_resp = MagicMock()
        cf_resp.status_code = 403
        cf_resp.text = "<title>Just a moment...</title>"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=cf_resp)

        # Playwright mock chain
        mock_page = AsyncMock()
        mock_page.content = AsyncMock(
            return_value='sources: [{file:"https://sv1.supervideo.cc/v/abc.mp4"}]'
        )
        mock_page.is_closed = MagicMock(return_value=False)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_pw = AsyncMock()
        mock_pw.chromium = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with patch(
            "scavengarr.infrastructure.hoster_resolvers.supervideo.async_playwright"
        ) as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)

            resolver = SuperVideoResolver(http_client=client)
            result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://sv1.supervideo.cc/v/abc.mp4"
        mock_page.goto.assert_awaited_once()
        mock_page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_cloudflare_404_no_playwright(self) -> None:
        """httpx 404 without Cloudflare markers does not trigger Playwright."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not found"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        with patch(
            "scavengarr.infrastructure.hoster_resolvers.supervideo.async_playwright"
        ) as mock_ap:
            resolver = SuperVideoResolver(http_client=client)
            result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is None
        # Playwright was never called
        mock_ap.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_closes_playwright(self) -> None:
        """cleanup() closes context, browser, and playwright."""
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = SuperVideoResolver(http_client=client)

        # Simulate Playwright having been started
        resolver._context = AsyncMock()
        resolver._browser = AsyncMock()
        resolver._playwright = AsyncMock()

        await resolver.cleanup()

        resolver._context is None
        resolver._browser is None
        resolver._playwright is None

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_playwright(self) -> None:
        """cleanup() is safe when Playwright was never started."""
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = SuperVideoResolver(http_client=client)

        # Should not raise
        await resolver.cleanup()

        assert resolver._playwright is None
        assert resolver._browser is None
        assert resolver._context is None

    @pytest.mark.asyncio
    async def test_browser_reuse_on_second_call(self) -> None:
        """_ensure_browser() short-circuits when browser already exists."""
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = SuperVideoResolver(http_client=client)

        mock_browser = AsyncMock()
        resolver._browser = mock_browser

        with patch(
            "scavengarr.infrastructure.hoster_resolvers.supervideo.async_playwright"
        ) as mock_ap:
            await resolver._ensure_browser()

        # Playwright never called — browser was already set
        mock_ap.assert_not_called()
        assert resolver._browser is mock_browser

    @pytest.mark.asyncio
    async def test_playwright_failure_returns_none(self) -> None:
        """When Playwright also fails, resolve returns None."""
        cf_resp = MagicMock()
        cf_resp.status_code = 403
        cf_resp.text = "<title>Just a moment...</title>"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=cf_resp)

        with patch(
            "scavengarr.infrastructure.hoster_resolvers.supervideo.async_playwright"
        ) as mock_ap:
            mock_ap.return_value.start = AsyncMock(
                side_effect=RuntimeError("No browser installed")
            )

            resolver = SuperVideoResolver(http_client=client)
            result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is None
