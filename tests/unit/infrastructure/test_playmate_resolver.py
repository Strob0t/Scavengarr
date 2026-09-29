"""Tests for PlaymateResolver (playmate.to)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.playmate import (
    PlaymateResolver,
    _extract_file_id,
)

_FID = "w2nGaorDAKY4S"
_META = f"https://playmate.to/api/video-meta?filecode={_FID}"
_API = "https://playmate.to/api/s"
_HLS = "https://frv1.plauymito.live/hls/EWKu4e7RfFOvkftB76Cmsufie9vfmehH/master.txt"


class TestExtractFileId:
    @pytest.mark.parametrize(
        "url",
        [
            f"https://playmate.to/watch/{_FID}",
            f"https://www.playmate.to/watch/{_FID}",
            f"https://playmate.to/e/{_FID}",
        ],
    )
    def test_valid(self, url: str) -> None:
        assert _extract_file_id(url) == _FID

    @pytest.mark.parametrize(
        "url", ["https://example.com/watch/w2nGaorDAKY4S", "https://playmate.to/", ""]
    )
    def test_invalid(self, url: str) -> None:
        assert _extract_file_id(url) is None


class TestPlaymateResolver:
    def test_name(self) -> None:
        assert PlaymateResolver(http_client=httpx.AsyncClient()).name == "playmate"

    @respx.mock
    async def test_resolves_hls_master(self) -> None:
        respx.get(_META).respond(200, json={"success": True, "title": "x"})
        api = respx.post(_API).respond(200, json={"cx": _FID, "sx": _HLS})

        async with httpx.AsyncClient() as client:
            result = await PlaymateResolver(http_client=client).resolve(
                f"https://playmate.to/watch/{_FID}"
            )

        assert result is not None
        assert result.video_url == _HLS
        assert result.is_hls is True  # master.txt is an HLS playlist
        assert result.quality == StreamQuality.UNKNOWN
        request = api.calls.last.request
        assert json.loads(request.content) == {"c": _FID, "d": "web"}
        assert request.headers["Origin"] == "https://playmate.to"
        assert request.headers["Referer"] == f"https://playmate.to/watch/{_FID}"
        # the API answers 403 to non-browser user agents
        assert request.headers["User-Agent"].startswith("Mozilla/5.0")

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (404, {"message": "Not found", "success": False}),
            (200, {"success": False}),
        ],
    )
    @respx.mock
    async def test_offline(self, status: int, body: dict[str, object]) -> None:
        respx.get(_META).respond(status, json=body)
        api = respx.post(_API)

        async with httpx.AsyncClient() as client:
            result = await PlaymateResolver(http_client=client).resolve(
                f"https://playmate.to/watch/{_FID}"
            )

        assert result is None
        assert not api.called

    @respx.mock
    async def test_api_forbidden(self) -> None:
        respx.get(_META).respond(200, json={"success": True})
        respx.post(_API).respond(403, json={"error": "forbidden"})

        async with httpx.AsyncClient() as client:
            result = await PlaymateResolver(http_client=client).resolve(
                f"https://playmate.to/watch/{_FID}"
            )

        assert result is None

    @respx.mock
    async def test_network_error(self) -> None:
        respx.get(_META).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            result = await PlaymateResolver(http_client=client).resolve(
                f"https://playmate.to/watch/{_FID}"
            )

        assert result is None
