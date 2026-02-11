"""Tests for VoeResolver — VOE hoster video URL extraction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.voe import (
    VoeResolver,
    _b64decode,
    _char_shift,
    _deobfuscate_mkgma,
    _extract_tokens_from_html,
    _is_valid_video_url,
    _parse_video_json,
    _replace_tokens,
    _rot13,
)


# ---------------------------------------------------------------------------
# Helper utilities tests
# ---------------------------------------------------------------------------
class TestRot13:
    def test_lowercase(self) -> None:
        assert _rot13("abc") == "nop"

    def test_uppercase(self) -> None:
        assert _rot13("ABC") == "NOP"

    def test_roundtrip(self) -> None:
        assert _rot13(_rot13("Hello World!")) == "Hello World!"

    def test_non_alpha(self) -> None:
        assert _rot13("123!@#") == "123!@#"


class TestReplaceTokens:
    def test_replaces_tokens(self) -> None:
        result = _replace_tokens("hello@$world^^test", ["@$", "^^"])
        assert result == "hello_world_test"

    def test_no_match(self) -> None:
        assert _replace_tokens("hello", ["@$"]) == "hello"


class TestCharShift:
    def test_shift_3(self) -> None:
        assert _char_shift("def", 3) == "abc"


class TestB64Decode:
    def test_standard(self) -> None:
        assert _b64decode("aGVsbG8=") == "hello"

    def test_without_padding(self) -> None:
        assert _b64decode("aGVsbG8") == "hello"


class TestIsValidVideoUrl:
    def test_valid_mp4(self) -> None:
        assert _is_valid_video_url("https://cdn.example.com/video.mp4") is True

    def test_valid_hls(self) -> None:
        assert _is_valid_video_url("https://cdn.example.com/master.m3u8") is True

    def test_bait_url(self) -> None:
        assert _is_valid_video_url("https://adserv.example.com/track.mp4") is False

    def test_not_http(self) -> None:
        assert _is_valid_video_url("ftp://example.com/video.mp4") is False


class TestExtractTokens:
    def test_extracts_from_html(self) -> None:
        html = """var x = ['@$','^^','~@','%?','*~','!!','#&'], y = 5;"""
        tokens = _extract_tokens_from_html(html)
        assert tokens == ["@$", "^^", "~@", "%?", "*~", "!!", "#&"]

    def test_returns_none_when_missing(self) -> None:
        assert _extract_tokens_from_html("<html></html>") is None


class TestParseVideoJson:
    def test_hls_file(self) -> None:
        data = {"file": "https://cdn.example.com/hls/master.m3u8", "source": None}
        assert _parse_video_json(data) == "https://cdn.example.com/hls/master.m3u8"

    def test_hls_source(self) -> None:
        data = {"source": "https://cdn.example.com/hls/master.m3u8"}
        assert _parse_video_json(data) == "https://cdn.example.com/hls/master.m3u8"

    def test_mp4_fallback(self) -> None:
        data = {
            "file": None,
            "fallbacks": [{"file": "https://cdn.example.com/video.mp4"}],
        }
        assert _parse_video_json(data) == "https://cdn.example.com/video.mp4"

    def test_empty_data(self) -> None:
        assert _parse_video_json({}) is None

    def test_non_http_ignored(self) -> None:
        data = {"file": "not-a-url"}
        assert _parse_video_json(data) is None


# ---------------------------------------------------------------------------
# MKGMa deobfuscation tests
# ---------------------------------------------------------------------------
class TestDeobfuscateMkgma:
    def test_returns_none_on_invalid_input(self) -> None:
        assert _deobfuscate_mkgma("invalid", ["@$"]) is None

    def test_returns_none_on_empty_tokens(self) -> None:
        assert _deobfuscate_mkgma("test", []) is None


# ---------------------------------------------------------------------------
# VoeResolver integration tests
# ---------------------------------------------------------------------------
class TestVoeResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = VoeResolver(http_client=client)
        assert resolver.name == "voe"

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_direct_mp4_regex(self) -> None:
        html = """
        <html><script>
        var config = {'mp4': 'https://cdn.voe.sx/video/abc123.mp4', 'other': 'x'};
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")

        assert result is not None
        assert result.video_url == "https://cdn.voe.sx/video/abc123.mp4"
        assert result.is_hls is False

    @pytest.mark.asyncio
    async def test_direct_hls_regex(self) -> None:
        html = """
        <html><script>
        var config = {'hls': 'https://cdn.voe.sx/engine/hls/master.m3u8'};
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")

        assert result is not None
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_engine_hls_url(self) -> None:
        html = """
        <html><script>
        var player = "https://delivery.voe.sx/engine/hls/abc123/master.m3u8";
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")

        assert result is not None
        assert "engine/hls" in result.video_url
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_b64_hls(self) -> None:
        import base64

        url = "https://cdn.voe.sx/engine/hls/master.m3u8"
        b64 = base64.b64encode(url.encode()).decode()
        html = f"""
        <html><script>
        var config = {{'hls': '{b64}'}};
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")

        assert result is not None
        assert result.video_url == url
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_b64_wc0_variable(self) -> None:
        import base64

        url = "https://cdn.voe.sx/video/abc123.mp4"
        b64 = base64.b64encode(url.encode()).decode()
        html = f"""
        <html><script>
        var wc0 = '{b64}';
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")

        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_b64_reversed_json(self) -> None:
        import base64
        import json

        video_data = json.dumps({"file": "https://cdn.voe.sx/engine/hls/master.m3u8"})
        # Reverse then base64 encode (the decoder reverses strings starting with "}")
        reversed_data = video_data[::-1]
        b64 = base64.b64encode(reversed_data.encode()).decode()
        # Wrap in a variable that looks like base64 starting with "ey"
        # Actually for this test we use wc0
        html = f"""
        <html><script>
        var wc0 = '{b64}';
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")

        assert result is not None
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_returns_none_when_all_methods_fail(self) -> None:
        html = "<html><body><h1>No video here</h1></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_filters_bait_urls(self) -> None:
        html = """
        <html><script>
        var config = {'mp4': 'https://adserv.tracker.com/banner.mp4'};
        </script></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = VoeResolver(http_client=client)
        result = await resolver.resolve("https://voe.sx/e/abc123")
        assert result is None
