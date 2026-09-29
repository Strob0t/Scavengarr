"""Tests for FilemoonResolver."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.browser.stealth_pool import CapturedMedia
from scavengarr.infrastructure.hoster_resolvers.filemoon import (
    FilemoonResolver,
    _extract_hls_from_unpacked,
    _unpack_p_a_c_k,
)


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

        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

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
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

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
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_file_deleted(self) -> None:
        html = "<html><body><p>This file was deleted.</p></body></html>"
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_source_found(self) -> None:
        html = "<html><body><h1>Player</h1></body></html>"
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_normalizes_download_url_to_embed(self) -> None:
        hls_url = "https://cdn.filemoon.sx/hls/abc/master.m3u8"
        packed = _build_packed_block(hls_url)
        html = f"<html><body><script>{packed}</script></body></html>"

        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

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
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

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
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_fake_signup(self) -> None:
        html = '<html><body><div class="fake-signup">Sign up</div></body></html>'
        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

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

        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

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

        html_resp = _make_html_response(html)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)

        resolver = FilemoonResolver(http_client=client)
        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url
        assert result.is_hls is True


class TestBrowserCapture:
    """Byse player pages (current Filemoon): the stream URL exists only once
    the player runs ("click play to verify you're a human", proof of work),
    so it is captured from the stealth browser."""

    _SPA = (
        "<html><head><title>Video | Byse</title>"
        '<script type="module" src="/assets/index-DocunfmE.js"></script>'
        '</head><body><div id="root"></div></body></html>'
    )

    def _resolver(
        self, html_resp: MagicMock, media: CapturedMedia | None
    ) -> tuple[FilemoonResolver, AsyncMock]:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=html_resp)
        pool = AsyncMock()
        pool.capture_media = AsyncMock(return_value=media)
        return FilemoonResolver(http_client=client, stealth_pool=pool), pool

    @pytest.mark.asyncio
    async def test_player_stream_is_captured(self) -> None:
        media = CapturedMedia(
            url="https://edge1.owphbf24.com/hls2/10/07008/x_h/master.m3u8?t=tok",
            referer="https://n1mwq.org/",
        )
        resolver, pool = self._resolver(_make_html_response(self._SPA), media)

        result = await resolver.resolve("https://filemoon.to/d/fwzwu9ny19jk")

        assert result is not None
        assert result.video_url == media.url
        assert result.is_hls is True
        assert result.headers == {"Referer": "https://n1mwq.org/"}
        pool.capture_media.assert_awaited_once()
        assert pool.capture_media.await_args.args[0] == (
            "https://filemoon.to/e/fwzwu9ny19jk"
        )

    @pytest.mark.asyncio
    async def test_mp4_stream_without_referer(self) -> None:
        media = CapturedMedia(url="https://cdn.example/v/x.mp4?t=1", referer=None)
        resolver, _ = self._resolver(_make_html_response(self._SPA), media)

        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.is_hls is False
        assert result.headers == {"Referer": "https://filemoon.sx/e/abc123def456"}

    @pytest.mark.asyncio
    async def test_nothing_captured_returns_none(self) -> None:
        resolver, _ = self._resolver(_make_html_response(self._SPA), None)

        assert await resolver.resolve("https://filemoon.sx/e/abc123def456") is None

    @pytest.mark.asyncio
    async def test_legacy_page_needs_no_browser(self) -> None:
        hls_url = "https://cdn.filemoon.sx/hls/abc/master.m3u8"
        html = (
            f"<html><body><script>{_build_packed_block(hls_url)}</script></body></html>"
        )
        resolver, pool = self._resolver(_make_html_response(html), None)

        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        assert result.video_url == hls_url
        pool.capture_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_offline_page_needs_no_browser(self) -> None:
        html = "<html><body><h1>File Not Found</h1></body></html>"
        resolver, pool = self._resolver(_make_html_response(html), None)

        assert await resolver.resolve("https://filemoon.sx/e/abc123def456") is None
        pool.capture_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_blocked_page_falls_back_to_browser(self) -> None:
        """Cloudflare or a geo block in front of the embed page."""
        media = CapturedMedia(url="https://cdn.example/x.m3u8", referer=None)
        resolver, pool = self._resolver(_make_html_response("", status=403), media)

        result = await resolver.resolve("https://filemoon.sx/e/abc123def456")

        assert result is not None
        pool.capture_media.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_without_browser_spa_page_returns_none(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_html_response(self._SPA))

        resolver = FilemoonResolver(http_client=client)

        assert await resolver.resolve("https://filemoon.sx/e/abc123def456") is None


class TestByseDetailsGate:
    """The Byse details API says up front when the browser cannot help."""

    _SPA = TestBrowserCapture._SPA

    @staticmethod
    def _client(details: MagicMock) -> AsyncMock:
        async def _get(url: str, **_: object) -> MagicMock:
            if "/api/videos/" in url:
                return details
            return _make_html_response(TestByseDetailsGate._SPA)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=_get)
        return client

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (404, '{"error":"video record missing: video not found"}'),
            (
                403,
                '{"error":"embedding from this domain is not allowed for this video"}',
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_dead_or_embed_restricted_skips_browser(
        self, status: int, body: str
    ) -> None:
        pool = AsyncMock()
        details = _make_html_response(body, status=status)
        resolver = FilemoonResolver(
            http_client=self._client(details), stealth_pool=pool
        )

        result = await resolver.resolve("https://bysezejataos.com/d/pz97syzbv14q")

        assert result is None
        pool.capture_media.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_available_video_goes_to_browser(self) -> None:
        pool = AsyncMock()
        pool.capture_media = AsyncMock(
            return_value=CapturedMedia(url="https://cdn.example/x.m3u8", referer=None)
        )
        details = _make_html_response('{"code":"fwzwu9ny19jk"}')
        resolver = FilemoonResolver(
            http_client=self._client(details), stealth_pool=pool
        )

        result = await resolver.resolve("https://filemoon.to/e/fwzwu9ny19jk")

        assert result is not None
        client_calls = [c.args[0] for c in resolver._http.get.await_args_list]
        assert "https://filemoon.to/api/videos/fwzwu9ny19jk/embed/details" in (
            client_calls
        )
