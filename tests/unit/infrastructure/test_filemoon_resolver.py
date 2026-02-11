"""Tests for FilemoonResolver."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.filemoon import (
    FilemoonResolver,
    _extract_hls_from_unpacked,
    _unpack_p_a_c_k,
)


def _make_api_response(data: dict, status: int = 200) -> MagicMock:  # type: ignore[type-arg]
    """Build a mock response that returns JSON data."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


def _make_html_response(html: str, status: int = 200) -> MagicMock:
    """Build a mock response for an HTML page."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    # json() should fail on HTML content
    resp.json.side_effect = json.JSONDecodeError("msg", "doc", 0)
    return resp


# -- Helper to build a realistic packed JS block --
def _build_packed_block(hls_url: str) -> str:
    """Build a minimal eval(function(p,a,c,k,e,d){...}) block.

    Uses base-36 encoding. The payload template has tokens 0-8 that
    get replaced from the dictionary.
    """
    # A simplified packed block that when unpacked yields JWPlayer config
    # We encode a simple payload: sources:[{file:"<hls_url>"}]
    # Using base 10 for simplicity in testing
    payload = "var 1=2('3');1.4({5:[{6:\\'7\\'}],8:\\'poster.jpg\\'});"
    keywords = [
        "",  # 0 (empty, keep as "0")
        "player",  # 1
        "jwplayer",  # 2
        "vplayer",  # 3
        "setup",  # 4
        "sources",  # 5
        "file",  # 6
        hls_url,  # 7
        "image",  # 8
    ]
    count = len(keywords)
    base = 10
    dict_str = "|".join(keywords)
    return (
        f"eval(function(p,a,c,k,e,d){{e=function(c)"
        f"{{return c.toString(a)}};if(!''.replace(/^/,String))"
        f"{{while(c--)d[c.toString(a)]=k[c]||c.toString(a);"
        f"k=[function(e){{return d[e]}}];e=function(){{return'\\\\w+'}}"
        f";c=1}};while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'"
        f"+e(c)+'\\\\b','g'),k[c]);return p}}"
        f"('{payload}',{base},{count},'{dict_str}'.split('|'),0,{{}}))"
    )


class TestUnpackPACK:
    def test_basic_unpack(self) -> None:
        url = "https://kken0rxqpr.cdn-jupiter.com/hls/abc/master.m3u8"
        packed = _build_packed_block(url)
        result = _unpack_p_a_c_k(packed)
        assert result is not None
        assert "jwplayer" in result
        assert url in result

    def test_returns_none_for_invalid_input(self) -> None:
        assert _unpack_p_a_c_k("not a packed block") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert _unpack_p_a_c_k("") is None

    def test_handles_base36_tokens(self) -> None:
        # Build a payload using higher base
        payload = (
            "var a=b('c');a.d({e:[{f:\\'https://cdn.example.com/master.m3u8\\'}]})"
        )
        keywords = [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "player",  # a
            "jwplayer",  # b
            "vplayer",  # c
            "setup",  # d
            "sources",  # e
            "file",  # f
        ]
        dict_str = "|".join(keywords)
        packed = (
            f"eval(function(p,a,c,k,e,d){{stuff}}"
            f"('{payload}',36,16,'{dict_str}'.split('|'),0,{{}}))"
        )
        result = _unpack_p_a_c_k(packed)
        assert result is not None
        assert "player" in result
        assert "jwplayer" in result


class TestExtractHlsFromUnpacked:
    def test_sources_array(self) -> None:
        js = """
        player.setup({
            sources:[{file:"https://cdn.example.com/hls/master.m3u8"}],
            image: "/poster.jpg"
        });
        """
        assert (
            _extract_hls_from_unpacked(js) == "https://cdn.example.com/hls/master.m3u8"
        )

    def test_file_property(self) -> None:
        js = """file:"https://cdn.example.com/video/master.m3u8?token=abc" """
        assert (
            _extract_hls_from_unpacked(js)
            == "https://cdn.example.com/video/master.m3u8?token=abc"
        )

    def test_source_property_mp4(self) -> None:
        js = """source:"https://cdn.example.com/video.mp4" """
        assert _extract_hls_from_unpacked(js) == "https://cdn.example.com/video.mp4"

    def test_no_match(self) -> None:
        assert _extract_hls_from_unpacked("var x = 42;") is None


class TestFilemoonResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = FilemoonResolver(http_client=client)
        assert resolver.name == "filemoon"

    @pytest.mark.asyncio
    async def test_extracts_hls_from_packed_js(self) -> None:
        hls_url = "https://kken0rxqpr.cdn-jupiter.com/hls/abc/master.m3u8"
        packed = _build_packed_block(hls_url)
        html = f"<html><head></head><body><script>{packed}</script></body></html>"

        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_extracts_direct_hls(self) -> None:
        html = """
        <html><body>
        <script>
        var src = "https://cdn.filemoon.sx/hls/abc/master.m3u8";
        </script>
        </body></html>
        """
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://cdn.filemoon.sx/hls/abc/master.m3u8"
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_file_not_found(self) -> None:
        html = "<html><body><h1>File Not Found</h1></body></html>"
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_file_deleted(self) -> None:
        html = "<html><body><p>This file was deleted.</p></body></html>"
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_source_found(self) -> None:
        html = "<html><body><h1>Player</h1></body></html>"
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_normalizes_download_url_to_embed(self) -> None:
        hls_url = "https://cdn.filemoon.sx/hls/abc/master.m3u8"
        packed = _build_packed_block(hls_url)
        html = f"<html><body><script>{packed}</script></body></html>"

        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/d/abc123def456")

        assert result is not None
        # Verify URL was normalized to /e/
        call_url = client.get.call_args[0][0]
        assert "/e/" in call_url

    @pytest.mark.asyncio
    async def test_normalizes_download_path_to_embed(self) -> None:
        html = (
            '<html><body><script>var x="https://a.com/v.m3u8";</script></body></html>'
        )
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        await resolver.resolve("https://filemoon.sx/download/abc123def456")

        call_url = client.get.call_args[0][0]
        assert "/e/" in call_url
        assert "/download/" not in call_url

    @pytest.mark.asyncio
    async def test_skips_thumbnail_m3u8_urls(self) -> None:
        html = """
        <html><body>
        <script>
        var thumb = "https://cdn.filemoon.sx/thumbnail/abc.m3u8";
        </script>
        </body></html>
        """
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_fake_signup(self) -> None:
        html = '<html><body><div class="fake-signup">Sign up</div></body></html>'
        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_packed_js_without_trailing_args(self) -> None:
        """Real Filemoon pages may omit the trailing ,0,{}) arguments."""
        hls_url = "https://cdn.filemoon.sx/hls/test/master.m3u8"
        packed = _build_packed_block(hls_url)
        # Strip the trailing ,0,{}) and close with just ))
        packed_no_trailing = packed.replace(",0,{}))", "))")
        html = f"<html><body><script>{packed_no_trailing}</script></body></html>"

        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_packed_js_with_extra_whitespace(self) -> None:
        """Packed JS blocks with extra whitespace between function params."""
        hls_url = "https://cdn.filemoon.sx/hls/ws/master.m3u8"
        packed = _build_packed_block(hls_url)
        # Add extra whitespace around function params
        packed_ws = packed.replace(
            "eval(function(p,a,c,k,e,d)",
            "eval( function( p , a , c , k , e , d )",
        )
        html = f"<html><body><script>{packed_ws}</script></body></html>"

        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url
        assert result.is_hls is True


class TestByseApi:
    """Tests for Filemoon Byse SPA API extraction."""

    @pytest.mark.asyncio
    async def test_byse_api_extracts_hls_url(self) -> None:
        api_data = {
            "sources": [
                {
                    "url": "https://cdn.filemoon.sx/hls/abc/master.m3u8",
                    "mimeType": "application/x-mpegURL",
                    "height": 720,
                },
            ],
        }
        api_resp = _make_api_response(api_data)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=api_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://cdn.filemoon.sx/hls/abc/master.m3u8"
        assert result.is_hls is True
        # Should only call the API, not fetch HTML
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_byse_api_extracts_mp4_url(self) -> None:
        api_data = {
            "sources": [
                {
                    "url": "https://cdn.filemoon.sx/v/abc.mp4",
                    "mimeType": "video/mp4",
                    "height": 1080,
                },
            ],
        }
        api_resp = _make_api_response(api_data)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=api_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://cdn.filemoon.sx/v/abc.mp4"
        assert result.is_hls is False

    @pytest.mark.asyncio
    async def test_byse_api_falls_through_on_404(self) -> None:
        """API returns 404 (expired video), falls through to legacy packed JS."""
        hls_url = "https://cdn.filemoon.sx/hls/fallback/master.m3u8"
        packed = _build_packed_block(hls_url)
        html = f"<html><body><script>{packed}</script></body></html>"

        api_resp = _make_api_response({}, status=404)
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url
        assert client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_byse_api_falls_through_on_network_error(self) -> None:
        """API call fails, falls through to legacy methods."""
        hls_url = "https://cdn.filemoon.sx/hls/net/master.m3u8"
        packed = _build_packed_block(hls_url)
        html = f"<html><body><script>{packed}</script></body></html>"

        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            side_effect=[httpx.ConnectError("api fail"), html_resp],
        )

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url

    @pytest.mark.asyncio
    async def test_byse_api_returns_none_on_empty_sources(self) -> None:
        """API returns valid JSON but empty sources array."""
        api_resp = _make_api_response({"sources": []})
        html_resp = _make_html_response("<html><body></body></html>")

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[api_resp, html_resp])

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_byse_api_nested_data_structure(self) -> None:
        """API returns sources nested under 'data' key."""
        api_data = {
            "data": {
                "sources": [
                    {
                        "url": "https://cdn.filemoon.sx/hls/nested/master.m3u8",
                        "mimeType": "application/x-mpegURL",
                    },
                ],
            },
        }
        api_resp = _make_api_response(api_data)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=api_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == "https://cdn.filemoon.sx/hls/nested/master.m3u8"

    def test_extract_video_id_from_embed(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = FilemoonResolver(http_client=client)
        assert resolver._extract_video_id("https://filemoon.sx/e/abc123") == "abc123"

    def test_extract_video_id_from_download(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = FilemoonResolver(http_client=client)
        assert resolver._extract_video_id("https://filemoon.sx/d/abc123") == "abc123"

    def test_extract_video_id_from_download_path(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = FilemoonResolver(http_client=client)
        assert (
            resolver._extract_video_id("https://filemoon.sx/download/abc123")
            == "abc123"
        )

    def test_extract_video_id_invalid_url(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = FilemoonResolver(http_client=client)
        assert resolver._extract_video_id("https://filemoon.sx/") == ""
