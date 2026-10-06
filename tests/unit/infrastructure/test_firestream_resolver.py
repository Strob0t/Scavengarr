"""Tests for FirestreamResolver (firestream.to / firestream.site)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.firestream import (
    FirestreamResolver,
    _extract_file_id,
)

_FID = "BKt92rb3"
_EMBED = f"https://firestream.to/e/{_FID}"
# firestream.to redirects the embed page to firestream.site
_FINAL = f"https://firestream.site/e/{_FID}?parent_ref="
_RESOLVE = f"https://firestream.site/api/videos/{_FID}/resolve"
_HLS = (
    "https://us-cdn-0.firestream.to/encodings/a/b/c/video.mp4/video.m3u8"
    "?md5=YTZQ&expires=1790671936"
)
_BLOB = "7Vggi0SWVr9FCeEfAqwr5S5sz9ZdkHwbnSj6MY5vp7v5+YLhzD0mL+wUFZR/"


def _page(status: str = "completed", blob: str | None = _BLOB) -> str:
    video = {"video": {"encodingStatus": status, "originalName": "x.mkv"}}
    parts = [
        "<html><head><title>FireStream Video</title></head><body>",
        f'<script id="video-data" type="application/json">{json.dumps(video)}</script>',
    ]
    if blob is not None:
        parts.append(f'<script id="token-blob" type="text/plain">\n{blob}\n</script>')
    return "".join(parts) + "</body></html>"


def _mock_embed(html: str, status: int = 200) -> None:
    respx.get(_EMBED).respond(302, headers={"Location": _FINAL})
    respx.get(_FINAL).respond(status, text=html)


class TestExtractFileId:
    @pytest.mark.parametrize(
        "url", [_EMBED, f"https://firestream.site/e/{_FID}?parent_ref="]
    )
    def test_valid(self, url: str) -> None:
        assert _extract_file_id(url) == _FID

    @pytest.mark.parametrize("url", ["https://example.com/e/BKt92rb3", ""])
    def test_invalid(self, url: str) -> None:
        assert _extract_file_id(url) is None

    @pytest.mark.parametrize("fid", ["777zhD-W", "OXIURmQ-", "jWcN-2XU", "a_b-c1D2"])
    def test_url_safe_base64_ids(self, fid: str) -> None:
        """IDs use the URL-safe base64 alphabet: filmpalast and moflix links
        with "-" were rejected as invalid (end-to-end test 2026-10-04), their
        pages play (encoding completed, token present)."""
        assert _extract_file_id(f"https://firestream.to/e/{fid}") == fid


class TestFirestreamResolver:
    def test_name_and_domains(self) -> None:
        resolver = FirestreamResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "firestream"
        assert {"firestream"} <= resolver.supported_domains

    @respx.mock
    async def test_resolves_signed_hls(self) -> None:
        _mock_embed(_page())
        api = respx.post(_RESOLVE).respond(
            200, json={"signedVideoUrl": _HLS, "signedVideoSdUrl": None}
        )

        async with httpx.AsyncClient() as client:
            result = await FirestreamResolver(http_client=client).resolve(_EMBED)

        assert result is not None
        assert result.video_url == _HLS
        assert result.is_hls is True
        # the token is only valid on the host that served the page
        assert json.loads(api.calls.last.request.content) == {"blob": _BLOB}

    @respx.mock
    async def test_dead_file(self) -> None:
        _mock_embed("Not found", status=404)

        async with httpx.AsyncClient() as client:
            assert await FirestreamResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_still_encoding(self) -> None:
        _mock_embed(_page(status="processing"))
        api = respx.post(_RESOLVE)

        async with httpx.AsyncClient() as client:
            assert await FirestreamResolver(http_client=client).resolve(_EMBED) is None
        assert not api.called

    @respx.mock
    async def test_missing_token(self) -> None:
        _mock_embed(_page(blob=None))

        async with httpx.AsyncClient() as client:
            assert await FirestreamResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_token_rejected(self) -> None:
        _mock_embed(_page())
        respx.post(_RESOLVE).respond(403, json={"error": "Token expired or invalid"})

        async with httpx.AsyncClient() as client:
            assert await FirestreamResolver(http_client=client).resolve(_EMBED) is None
