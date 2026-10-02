"""Tests for GxplayerResolver (watch.gxplayer.xyz)."""

from __future__ import annotations

import httpx
import pytest
import respx
from scavengarr.infrastructure.hoster_resolvers.gxplayer import (
    GxplayerResolver,
    _extract_video_id,
)

from scavengarr.domain.entities.stremio import StreamQuality

_VID = "4QGFS5I1"
_URL = f"https://watch.gxplayer.xyz/watch?v={_VID}"
_MD5 = "cf43a9e6874c5afbebe2858a64d45f52"
_MASTER = f"https://watch.gxplayer.xyz/m3u8/3/{_MD5}/master.txt?s=1&id=11256&cache=1"

# The video object of a live watch page (megakino's "Stream in HD")
_PAGE = (
    "<html><head><title>Oppenheimer.mp4</title></head><body><script>"
    'var video = {"id":"11256","uid":"3","slug":"4QGFS5I1",'
    '"title":"Oppenheimer.mp4","folderid":null,'
    '"quality":"[\\"720p\\",\\"480p\\",\\"240p\\"]","sources":null,'
    '"type":"hls","status":"1","progress":"all done.",'
    f'"md5":"{_MD5}"}};'
    "</script></body></html>"
)
_GONE = (
    '<html><head><title>Warning</title></head><body><div id="sun">'
    "<h1>404 Error</h1><p>Video is not found.</p></div></body></html>"
)


class TestExtractVideoId:
    @pytest.mark.parametrize(
        "url",
        [
            _URL,
            f"http://watch.gxplayer.xyz/watch?v={_VID}",
            f"https://watch.gxplayer.xyz/watch?v={_VID}&autoplay=true",
            f"https://gxplayer.xyz/watch?v={_VID}",
        ],
    )
    def test_valid(self, url: str) -> None:
        assert _extract_video_id(url) == _VID

    @pytest.mark.parametrize(
        "url",
        [
            f"https://example.com/watch?v={_VID}",
            "https://watch.gxplayer.xyz/watch?v=4QGFS",
            "https://watch.gxplayer.xyz/watch",
            f"https://watch.gxplayer.xyz/embed?v={_VID}",
            "",
        ],
    )
    def test_invalid(self, url: str) -> None:
        assert _extract_video_id(url) is None


class TestGxplayerResolver:
    def test_name(self) -> None:
        assert GxplayerResolver(http_client=httpx.AsyncClient()).name == "gxplayer"

    @respx.mock
    async def test_resolves_hls_master(self) -> None:
        respx.get(_URL).respond(200, text=_PAGE)

        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(_URL)

        assert result is not None
        assert result.video_url == _MASTER
        assert result.is_hls is True  # master.txt is an HLS playlist
        assert result.quality == StreamQuality.UNKNOWN
        assert not result.headers  # the CDN needs no Referer

    @respx.mock
    async def test_master_on_the_host_that_served_the_page(self) -> None:
        respx.get(_URL).respond(
            302, headers={"Location": f"https://watch2.gxplayer.xyz/watch?v={_VID}"}
        )
        respx.get(f"https://watch2.gxplayer.xyz/watch?v={_VID}").respond(
            200, text=_PAGE
        )

        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(_URL)

        assert result is not None
        assert result.video_url.startswith("https://watch2.gxplayer.xyz/m3u8/3/")

    @pytest.mark.parametrize(("status", "body"), [(200, _GONE), (404, "")])
    @respx.mock
    async def test_gone(self, status: int, body: str) -> None:
        respx.get(_URL).respond(status, text=body)

        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(_URL)

        assert result is None

    @respx.mock
    async def test_page_without_video_data(self) -> None:
        respx.get(_URL).respond(200, text="<html><body>maintenance</body></html>")

        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(_URL)

        assert result is None

    @respx.mock
    async def test_server_error(self) -> None:
        respx.get(_URL).respond(503, text="busy")

        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(_URL)

        assert result is None

    @respx.mock
    async def test_network_error(self) -> None:
        respx.get(_URL).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(_URL)

        assert result is None

    async def test_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await GxplayerResolver(http_client=client).resolve(
                "https://example.com/watch?v=4QGFS5I1"
            )

        assert result is None
