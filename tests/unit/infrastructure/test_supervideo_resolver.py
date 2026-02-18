"""Tests for SuperVideoResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.cloudflare import (
    is_cloudflare_challenge,
)
from scavengarr.infrastructure.hoster_resolvers.supervideo import (
    SuperVideoResolver,
    _extract_html5_video,
    _extract_jwplayer_source,
    _extract_packed_eval,
    _unpack_p_a_c_k,
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
    """Kept for backward compat; delegates to shared cloudflare module."""

    def test_403_with_just_a_moment(self) -> None:
        assert is_cloudflare_challenge(403, "<title>Just a moment...</title>") is True

    def test_503_with_challenge_platform(self) -> None:
        assert is_cloudflare_challenge(503, '<div id="challenge-platform">') is True

    def test_403_with_cf_error_details(self) -> None:
        assert is_cloudflare_challenge(403, '<div id="cf-error-details">') is True

    def test_403_with_challenge_platform_text(self) -> None:
        assert is_cloudflare_challenge(403, "challenge-platform") is True

    def test_403_without_cloudflare_markers(self) -> None:
        assert is_cloudflare_challenge(403, "<html>Forbidden</html>") is False

    def test_200_with_just_a_moment(self) -> None:
        assert is_cloudflare_challenge(200, "Just a moment") is False

    def test_404_plain(self) -> None:
        assert is_cloudflare_challenge(404, "Not found") is False


class TestUnpackPACK:
    """Tests for Dean Edwards' p.a.c.k.e.r decoder."""

    def test_decodes_base36_packed_js(self) -> None:
        """Decode a base-36 packed block with JWPlayer file URL."""
        # Simplified packed JS: base=10, tokens map digits to words
        # Body: "file:'1://2.3.4/5/6.7'" with base=10
        # tokens: ["", "https", "cdn", "example", "com", "hls", "video", "m3u8", ...]
        packed = (
            "eval(function(p,a,c,k,e,d)"
            "{e=function(c){return c.toString(a)};"
            "while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);"
            "return p}"
            "('file:\"1://2.3.4/5/6.7\"',10,8,"
            "'|https|cdn|example|com|hls|video|m3u8'.split('|')))"
        )
        result = _unpack_p_a_c_k(packed)
        assert result is not None
        assert 'file:"https://cdn.example.com/hls/video.m3u8"' in result

    def test_returns_none_for_non_packed(self) -> None:
        assert _unpack_p_a_c_k("var x = 42;") is None

    def test_returns_none_for_invalid_base(self) -> None:
        """Base out of range (0 or >36) returns None."""
        packed = "('body',0,0,''.split('|'))"
        assert _unpack_p_a_c_k(packed) is None

    def test_handles_empty_tokens(self) -> None:
        """Tokens with empty entries leave original number in place."""
        packed = "('0 1 2',10,3,'hello||world'.split('|'))"
        result = _unpack_p_a_c_k(packed)
        assert result is not None
        assert "hello" in result
        assert "world" in result
        # Token index 1 is empty, so "1" stays as "1"
        assert "1" in result


class TestExtractPackedEval:
    """Tests for extracting video URLs from packed eval() JS."""

    def test_extracts_url_from_packed_jwplayer(self) -> None:
        """Full eval() block with JWPlayer file URL in tokens."""
        html = """<html><script>
        eval(function(p,a,c,k,e,d){e=function(c){return c.toString(a)};
        while(c--)if(k[c])p=p.replace(new RegExp('\\b'+e(c)+'\\b','g'),k[c]);
        return p}('file:\"1://2.3.4/5/6.7\"',10,8,
        '|https|cdn|example|com|hls|video|m3u8'.split('|')))
        </script></html>"""
        result = _extract_packed_eval(html)
        assert result is not None
        assert "https://cdn.example.com/hls/video.m3u8" in result

    def test_extracts_literal_url_in_packed_block(self) -> None:
        """Packed block containing a literal URL (not encoded)."""
        html = """<html><script>
        eval(function(p,a,c,k,e,d){return p}(
        'var player = "https://cdn.example.com/video.mp4"',10,0,
        ''.split('|')))
        </script></html>"""
        result = _extract_packed_eval(html)
        assert result == "https://cdn.example.com/video.mp4"

    def test_returns_none_when_no_eval_block(self) -> None:
        html = "<html><body>No packed JS here</body></html>"
        assert _extract_packed_eval(html) is None

    def test_resolver_uses_packed_eval_fallback(self) -> None:
        """Integration: resolve() falls through JWPlayer/HTML5 to packed eval."""
        html = """<html><script>
        eval(function(p,a,c,k,e,d){e=function(c){return c.toString(a)};
        while(c--)if(k[c])p=p.replace(new RegExp('\\b'+e(c)+'\\b','g'),k[c]);
        return p}('file:\"1://2.3.4/5/6.7\"',10,8,
        '|https|cdn|example|com|hls|video|m3u8'.split('|')))
        </script></html>"""
        resolver = SuperVideoResolver(http_client=MagicMock(spec=httpx.AsyncClient))
        result = resolver._extract_video(html, "https://supervideo.cc/e/test")
        assert result is not None
        assert result.is_hls is True
        assert "m3u8" in result.video_url


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

        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(return_value=head_resp)

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

        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(return_value=head_resp)

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

        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(return_value=head_resp)

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

        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(return_value=head_resp)

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

        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(return_value=head_resp)

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

    @pytest.mark.asyncio
    async def test_returns_none_when_head_verification_fails(self) -> None:
        """Video URL extracted but HEAD returns 403 → None."""
        html = """
        <html><script>
        sources: [{file:"https://sv1.supervideo.cc/v/abc.mp4"}]
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        head_resp = MagicMock()
        head_resp.status_code = 403

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(return_value=head_resp)

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")
        assert result is None
        client.head.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_head_network_error(self) -> None:
        """Video URL extracted but HEAD network error → None."""
        html = """
        <html><script>
        sources: [{file:"https://sv1.supervideo.cc/v/abc.mp4"}]
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))

        resolver = SuperVideoResolver(http_client=client)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")
        assert result is None


class TestSuperVideoPlaywrightFallback:
    """Tests for Cloudflare detection and StealthPool fallback."""

    @pytest.mark.asyncio
    async def test_cloudflare_403_triggers_stealth_fallback(self) -> None:
        """httpx 403 + 'Just a moment' triggers StealthPool fallback."""
        # httpx returns Cloudflare block
        cf_resp = MagicMock()
        cf_resp.status_code = 403
        cf_resp.text = "<title>Just a moment...</title>"

        head_resp = MagicMock()
        head_resp.status_code = 200

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=cf_resp)
        client.head = AsyncMock(return_value=head_resp)

        # StealthPool mock
        mock_page = AsyncMock()
        mock_page.content = AsyncMock(
            return_value='sources: [{file:"https://sv1.supervideo.cc/v/abc.mp4"}]'
        )
        mock_page.is_closed = MagicMock(return_value=False)

        mock_pool = AsyncMock()
        mock_pool.new_page = AsyncMock(return_value=mock_page)
        mock_pool.wait_for_cloudflare = AsyncMock()

        resolver = SuperVideoResolver(
            http_client=client,
            stealth_pool=mock_pool,
        )
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://sv1.supervideo.cc/v/abc.mp4"
        mock_pool.new_page.assert_awaited_once()
        mock_pool.wait_for_cloudflare.assert_awaited_once()
        mock_page.goto.assert_awaited_once()
        mock_page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_cloudflare_404_no_stealth(self) -> None:
        """httpx 404 without Cloudflare markers does not trigger StealthPool."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not found"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        mock_pool = AsyncMock()

        resolver = SuperVideoResolver(
            http_client=client,
            stealth_pool=mock_pool,
        )
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is None
        # StealthPool was never called
        mock_pool.new_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stealth_pool_skips_playwright(self) -> None:
        """When stealth_pool is None, Playwright fallback is skipped."""
        cf_resp = MagicMock()
        cf_resp.status_code = 403
        cf_resp.text = "<title>Just a moment...</title>"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=cf_resp)

        resolver = SuperVideoResolver(http_client=client, stealth_pool=None)
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is None

    @pytest.mark.asyncio
    async def test_stealth_pool_failure_returns_none(self) -> None:
        """When StealthPool raises, resolve returns None."""
        cf_resp = MagicMock()
        cf_resp.status_code = 403
        cf_resp.text = "<title>Just a moment...</title>"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=cf_resp)

        mock_pool = AsyncMock()
        mock_pool.new_page = AsyncMock(side_effect=RuntimeError("No browser installed"))

        resolver = SuperVideoResolver(
            http_client=client,
            stealth_pool=mock_pool,
        )
        result = await resolver.resolve("https://supervideo.cc/e/abc123def456")

        assert result is None
