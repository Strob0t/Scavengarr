"""Tests for the vinovo.to resolver (player API stream token, no captcha)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.vinovo import (
    VinovoResolver,
    _extract_file_id,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

_FID = "9qn0yg5dhje0ny"
_EMBED = f"https://vinovo.to/e/{_FID}"
_API = f"https://vinovo.to/api/file/url/{_FID}"
_CDN = "https://fs-715f11.vincdn.net"
_PAGE_TOKEN = "0123456789abcdef0123456789abcdef"
# Shapes captured from the live site (2026-09-29)
_EMBED_HTML = (
    "<html><head><title>tt1228705.Iron.Man.2.2010.Bluray-1080p-VideoStar - VINOVO"
    f'</title><meta name="token" content="{_PAGE_TOKEN}"></head><body>'
    f'<div id="player" data-base="{_CDN}"></div></body></html>'
)
_OFFLINE_HTML = (
    f'<html><head><meta name="token" content="{_PAGE_TOKEN}"></head><body>'
    '<div class="container error"><h1>Not found</h1><p>Video not found</p>'
    "</div></body></html>"
)
_STREAM_TOKEN = f"{_FID}/VU3BOndEBJerWQ8ugSNoiw/1790686633"


class TestExtractFileId:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_EMBED, _FID),
            (f"https://vinovo.to/d/{_FID}", _FID),
            (f"https://vinovo.si/e/{_FID}/", _FID),
            ("https://vinovo.to/xyz", None),
            (f"https://veev.to/e/{_FID}", None),
        ],
    )
    def test_urls(self, url: str, expected: str | None) -> None:
        assert _extract_file_id(url) == expected


class TestVinovoResolver:
    def test_name(self) -> None:
        assert VinovoResolver(http_client=httpx.AsyncClient()).name == "vinovo"

    def test_declares_its_cdn_address_bound(self) -> None:
        assert VinovoResolver.address_bound is True

    @respx.mock
    async def test_resolves_stream_url(self) -> None:
        respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        api = respx.post(_API).respond(
            200, json={"status": "ok", "token": _STREAM_TOKEN}
        )

        async with httpx.AsyncClient() as client:
            result = await VinovoResolver(http_client=client).resolve(
                f"https://vinovo.to/d/{_FID}"
            )

        assert result is not None
        assert result.video_url == f"{_CDN}/stream/{_STREAM_TOKEN}"
        # the CDN binds the token to the resolving User-Agent
        assert result.headers == {
            "Referer": "https://vinovo.to/",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        request = api.calls[0].request
        assert request.content == f"recaptcha=&token={_PAGE_TOKEN}".encode()
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"
        assert request.headers["User-Agent"] == DEFAULT_USER_AGENT

    @respx.mock
    async def test_offline_file(self) -> None:
        respx.get(_EMBED).respond(200, text=_OFFLINE_HTML)
        api = respx.post(_API)

        async with httpx.AsyncClient() as client:
            assert await VinovoResolver(http_client=client).resolve(_EMBED) is None
        assert not api.called

    @respx.mock
    async def test_api_error(self) -> None:
        respx.get(_EMBED).respond(200, text=_EMBED_HTML)
        respx.post(_API).respond(200, json={"status": "error", "message": "nope"})

        async with httpx.AsyncClient() as client:
            assert await VinovoResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_missing_page_token(self) -> None:
        respx.get(_EMBED).respond(200, text="<html><body>player</body></html>")

        async with httpx.AsyncClient() as client:
            assert await VinovoResolver(http_client=client).resolve(_EMBED) is None

    @respx.mock
    async def test_network_error(self) -> None:
        respx.get(_EMBED).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            assert await VinovoResolver(http_client=client).resolve(_EMBED) is None

    async def test_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            resolver = VinovoResolver(http_client=client)
            assert await resolver.resolve("https://vinovo.to/xyz") is None
