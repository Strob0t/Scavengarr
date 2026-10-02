"""Tests for MixdropResolver (MP4 from the embed player's packed setup)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.mixdrop import (
    MixdropResolver,
    _extract_file_id,
)

_EMBED = "https://mxdrop.to/e/1vw60ojqto7ll7"
_CURRENT_EMBED = "https://mxdrop.top/e/1vw60ojqto7ll7"
_VIDEO = "https://a-delivery9.mxcontent.net/v2/abc.mp4?s=tok&e=x"

# The player's setup as the site packs it (Dean Edwards' packer, base 62):
# words 0 and 1 stand for "MDCore" and "wurl"
_PLAYER = (
    "<html><body><script>eval(function(p,a,c,k,e,d){while(c--)if(k[c])"
    "p=p.replace(new RegExp('\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}"
    '(\'0.poster="//a-delivery9.mxcontent.net/thumbs/abc.jpg";'
    '0.1="//a-delivery9.mxcontent.net/v2/abc.mp4?s=tok&e=x";\','
    "62,2,'MDCore|wurl'.split('|'),0,{}))</script></body></html>"
)
# A deleted file's player sets no wurl
_DELETED = (
    "<html><body><script>eval(function(p,a,c,k,e,d){return p}"
    "('0.1=\"\";',62,2,'MDCore|wurl'.split('|'),0,{}))</script>"
    "<h2>WE ARE SORRY</h2></body></html>"
)


class TestExtractFileId:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://mxdrop.to/e/1vw60ojqto7ll7", "1vw60ojqto7ll7"),
            ("https://mixdrop.ag/f/abc123def/Movie.mkv", "abc123def"),
            ("https://www.m1xdrop.net/emb/abc123", "abc123"),
            ("http://mixdrop23.net/e/abc123", "abc123"),
            ("https://mxdrop.to/thumbs/abc123.jpg", None),
            ("https://voe.sx/e/abc123", None),
        ],
    )
    def test_file_id(self, url: str, expected: str | None) -> None:
        assert _extract_file_id(url) == expected


class TestResolver:
    def test_name_and_domains(self) -> None:
        resolver = MixdropResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "mixdrop"
        assert {"mixdrop", "mxdrop", "m1xdrop"} <= resolver.supported_domains

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_the_mp4_of_the_player(self) -> None:
        respx.get(_EMBED).respond(301, headers={"Location": _CURRENT_EMBED})
        respx.get(_CURRENT_EMBED).respond(200, text=_PLAYER)

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(_EMBED)

        assert result is not None
        assert result.video_url == _VIDEO
        assert result.is_hls is False
        assert result.headers == {"Referer": _CURRENT_EMBED}

    @respx.mock
    @pytest.mark.asyncio()
    async def test_file_page_is_read_through_the_embed_player(self) -> None:
        embed = respx.get("https://mixdrop.ag/e/abc123def").respond(200, text=_PLAYER)

        async with httpx.AsyncClient() as client:
            result = await MixdropResolver(http_client=client).resolve(
                "https://mixdrop.ag/f/abc123def/Movie.mkv"
            )

        assert embed.called
        assert result is not None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_deleted_file_is_offline(self) -> None:
        respx.get(_EMBED).respond(200, text=_DELETED)

        async with httpx.AsyncClient() as client:
            assert await MixdropResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_error_status_is_offline(self) -> None:
        respx.get(_EMBED).respond(404)

        async with httpx.AsyncClient() as client:
            assert await MixdropResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_network_error_is_left_to_the_registry(self) -> None:
        # The registry does not cache a network error as a dead link
        respx.get(_EMBED).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await MixdropResolver(http_client=client).resolve(_EMBED)

    @pytest.mark.asyncio()
    async def test_foreign_url_is_rejected(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = MixdropResolver(http_client=client)
            assert await resolver.resolve("https://voe.sx/e/abc123") is None
