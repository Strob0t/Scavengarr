"""Tests for FsstResolver (fsst.online, kinoger's first player tab)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.fsst import (
    FsstResolver,
    _extract_file_id,
)

_EMBED = "https://fsst.online/embed/992734/"
_PLAYER = "https://incvideo1.online/embed/992734/"
_GET_FILE = "https://www.incvideo1.online/get_file/16/{hash}/992000/992734/{name}/"
_360 = _GET_FILE.format(hash="feec0c37b07fb3b344", name="992734_360p.mp4")
_720 = _GET_FILE.format(hash="d404fbe46d0c317de2", name="992734_720p.mp4")
_1080 = _GET_FILE.format(hash="47bcee2a8f883c4d65", name="992734.mp4")


def _player_page(file: str) -> str:
    """The Playerjs setup of a live embed page (Kernel Video Sharing)."""
    return (
        "<html><head><title>john.wick.kapitel.4.2023.german.dl.1080p.mkv"
        "</title></head><body><script>\n"
        "var player = new Playerjs({\n"
        "\t\t\t\tid: 'Video992734',\n"
        '\t\t\t\tsubtitle: "",\n'
        "\t\t\t\tposter: 'https://www.incvideo1.online/contents/"
        "videos_screenshots/992000/992734/preview.jpg',\n"
        f'\t\t\t\tfile:"{file}",\n'
        "\t\t\t\turl: 'https://fsst.online/videos/992734/john-wick/',\n"
        '\t\t\t\tembed: "https://fsst.online/embed/992734/",\n'
        "});\n</script></body></html>"
    )


_ALL_QUALITIES = f"[360p]{_360},[720p]{_720},[1080p]{_1080}"
_GONE = (
    "<html><head><title>404</title></head><body><script>"
    "var player = new Playerjs({ id: 'Video404', "
    "file: '/contents/other/video_error.mp4' });</script></body></html>"
)


class TestExtractFileId:
    @pytest.mark.parametrize(
        "url",
        [
            _EMBED,
            "https://fsst.online/embed/992734",
            "http://www.fsst.online/embed/992734/",
            "https://fsst.online/videos/992734/john-wick-kapitel-4-2023/",
        ],
    )
    def test_valid(self, url: str) -> None:
        assert _extract_file_id(url) == "992734"

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/embed/992734/",
            "https://fsst.online/embed/abc/",
            "https://fsst.online/",
            "",
        ],
    )
    def test_invalid(self, url: str) -> None:
        assert _extract_file_id(url) is None


class TestFsstResolver:
    def test_name(self) -> None:
        assert FsstResolver(http_client=httpx.AsyncClient()).name == "fsst"

    @respx.mock
    async def test_resolves_the_best_quality(self) -> None:
        respx.get(_EMBED).respond(302, headers={"Location": _PLAYER})
        respx.get(_PLAYER).respond(200, text=_player_page(_ALL_QUALITIES))

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is not None
        assert result.video_url == _1080
        assert result.is_hls is False
        assert result.quality == StreamQuality.UNKNOWN
        assert not result.headers  # get_file links need no Referer

    @respx.mock
    async def test_qualities_in_any_order(self) -> None:
        respx.get(_EMBED).respond(
            200, text=_player_page(f"[720p]{_720},[1080p]{_1080},[360p]{_360}")
        )

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is not None
        assert result.video_url == _1080

    @respx.mock
    async def test_single_unlabelled_file(self) -> None:
        respx.get(_EMBED).respond(200, text=_player_page(_720))

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is not None
        assert result.video_url == _720

    @respx.mock
    async def test_video_page_link_reads_the_embed_page(self) -> None:
        embed = respx.get(_EMBED).respond(200, text=_player_page(_ALL_QUALITIES))

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(
                "https://fsst.online/videos/992734/john-wick-kapitel-4-2023/"
            )

        assert result is not None
        assert embed.called

    @pytest.mark.parametrize(("status", "body"), [(404, _GONE), (200, _GONE)])
    @respx.mock
    async def test_gone(self, status: int, body: str) -> None:
        respx.get(_EMBED).respond(status, text=body)

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is None

    @respx.mock
    async def test_page_without_player(self) -> None:
        respx.get(_EMBED).respond(200, text="<html><body>maintenance</body></html>")

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is None

    @respx.mock
    async def test_server_error(self) -> None:
        respx.get(_EMBED).respond(502, text="bad gateway")

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is None

    @respx.mock
    async def test_network_error(self) -> None:
        respx.get(_EMBED).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(_EMBED)

        assert result is None

    async def test_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await FsstResolver(http_client=client).resolve(
                "https://example.com/embed/992734/"
            )

        assert result is None
