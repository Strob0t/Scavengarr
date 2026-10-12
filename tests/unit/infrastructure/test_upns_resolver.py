"""Tests for UpnsResolver (UPN Share ``upns`` and RPM Share ``rpmplay``)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.hoster_resolvers.upns import (
    UpnsResolver,
    _extract_file_id,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

_KEY = b"kiemtienmua911ca"
_IV = b"1234567890oiuytr"
_OTHER_IV = b"0123456789abcdef"

_UPNS_URL = "https://moflix.upns.xyz/#n8wux6"
_RPMPLAY_URL = "https://moflix.rpmplay.xyz/#wqjv9"
_UPNS_API = "https://moflix.upns.xyz/api/v1/video"
_RPMPLAY_API = "https://moflix.rpmplay.xyz/api/v1/video"
_HLS_URL = "https://s1.example-cdn.net/hls/n8wux6/master.m3u8"
_NOT_FOUND = '{"message": "Video not found or deleted"}'
_RATE_LIMITED = '{"message": "Rate limit exceeded"}'
_HLS_HEADERS = {"content-type": "application/vnd.apple.mpegurl"}


def _encrypt(plain: str, iv: bytes = _IV) -> str:
    """Hex ciphertext the player's API answers with (AES-128-CBC, PKCS7)."""
    data = plain.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(_KEY), modes.CBC(iv)).encryptor()
    return (encryptor.update(data) + encryptor.finalize()).hex()


def _answer(source: str = _HLS_URL, iv: bytes = _IV) -> str:
    return _encrypt(
        json.dumps({"title": "n8wux6", "poster": "/p.jpg", "source": source}), iv
    )


class TestExtractFileId:
    def test_upns_fragment(self) -> None:
        assert _extract_file_id(_UPNS_URL) == "n8wux6"

    def test_rpmplay_fragment(self) -> None:
        assert _extract_file_id(_RPMPLAY_URL) == "wqjv9"

    def test_www_prefix_and_http_scheme(self) -> None:
        assert _extract_file_id("http://www.moflix.upns.xyz/#n8wux6") == "n8wux6"

    def test_fragment_with_a_player_suffix(self) -> None:
        assert (
            _extract_file_id("https://moflix.upns.xyz/#n8wux6&autoplay=1") == "n8wux6"
        )
        assert _extract_file_id("https://moflix.upns.xyz/#n8wux6/extra") == "n8wux6"

    def test_no_fragment(self) -> None:
        assert _extract_file_id("https://moflix.upns.xyz/") is None
        assert _extract_file_id("https://moflix.upns.xyz/n8wux6") is None

    def test_short_or_odd_id_rejected(self) -> None:
        assert _extract_file_id("https://moflix.upns.xyz/#abc") is None
        assert _extract_file_id("https://moflix.upns.xyz/#ab-cd") is None

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/#n8wux6") is None

    def test_empty_and_invalid(self) -> None:
        assert _extract_file_id("") is None
        assert _extract_file_id("not-a-url") is None


class TestUpnsResolver:
    def test_names(self) -> None:
        client = httpx.AsyncClient()
        assert UpnsResolver(http_client=client).name == "upns"
        assert UpnsResolver(http_client=client, hoster="rpmplay").name == "rpmplay"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_the_hls_master(self) -> None:
        api = respx.get(_UPNS_API).respond(
            200, content=_answer(), headers={"content-type": "application/octet-stream"}
        )
        respx.head(_HLS_URL).respond(200, headers=_HLS_HEADERS)
        async with httpx.AsyncClient() as client:
            stream = await UpnsResolver(http_client=client).resolve(_UPNS_URL)

        assert stream is not None
        assert stream.video_url == _HLS_URL
        assert stream.is_hls is True
        assert stream.quality == StreamQuality.UNKNOWN
        assert stream.headers == {
            "Origin": "https://moflix.upns.xyz",
            "Referer": "https://moflix.upns.xyz/",
        }
        request = api.calls.last.request
        assert dict(request.url.params) == {
            "id": "n8wux6",
            "w": "1280",
            "h": "720",
            "r": "",
        }
        assert request.headers["Referer"] == "https://moflix.upns.xyz/"
        assert request.headers["User-Agent"] == DEFAULT_USER_AGENT

    @respx.mock
    @pytest.mark.asyncio()
    async def test_rpmplay_asks_its_own_host(self) -> None:
        api = respx.get(_RPMPLAY_API).respond(200, content=_answer())
        respx.head(_HLS_URL).respond(200, headers=_HLS_HEADERS)
        async with httpx.AsyncClient() as client:
            resolver = UpnsResolver(http_client=client, hoster="rpmplay")
            stream = await resolver.resolve(_RPMPLAY_URL)

        assert stream is not None
        assert api.calls.last.request.url.params["id"] == "wqjv9"
        assert stream.headers["Origin"] == "https://moflix.rpmplay.xyz"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_second_iv_is_tried(self) -> None:
        respx.get(_UPNS_API).respond(200, content=_answer(iv=_OTHER_IV))
        respx.head(_HLS_URL).respond(200, headers=_HLS_HEADERS)
        async with httpx.AsyncClient() as client:
            stream = await UpnsResolver(http_client=client).resolve(_UPNS_URL)
        assert stream is not None and stream.video_url == _HLS_URL

    @respx.mock
    @pytest.mark.asyncio()
    async def test_bytes_after_the_json_object_are_ignored(self) -> None:
        plain = json.dumps({"source": _HLS_URL}) + "\x00\x00"
        respx.get(_UPNS_API).respond(200, content=_encrypt(plain))
        respx.head(_HLS_URL).respond(200, headers=_HLS_HEADERS)
        async with httpx.AsyncClient() as client:
            stream = await UpnsResolver(http_client=client).resolve(_UPNS_URL)
        assert stream is not None and stream.video_url == _HLS_URL

    @respx.mock
    @pytest.mark.asyncio()
    async def test_an_mp4_source_is_no_hls(self) -> None:
        mp4 = "https://s1.example-cdn.net/files/n8wux6.mp4?t=1"
        respx.get(_UPNS_API).respond(200, content=_answer(source=mp4))
        respx.head(mp4).respond(200, headers={"content-type": "video/mp4"})
        async with httpx.AsyncClient() as client:
            stream = await UpnsResolver(http_client=client).resolve(_UPNS_URL)
        assert stream is not None and stream.is_hls is False

    @respx.mock
    @pytest.mark.asyncio()
    async def test_deleted_video_is_offline(self) -> None:
        respx.get(_UPNS_API).respond(404, content=_NOT_FOUND)
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_rate_limit_is_not_resolved(self) -> None:
        respx.get(_UPNS_API).respond(429, content=_RATE_LIMITED)
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_http_error(self) -> None:
        respx.get(_UPNS_API).respond(500)
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_network_error(self) -> None:
        respx.get(_UPNS_API).mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_answer_that_is_no_ciphertext(self) -> None:
        respx.get(_UPNS_API).respond(200, content="<html>blocked</html>")
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_ciphertext_of_another_key(self) -> None:
        # Decrypts to garbage with every IV (no PKCS7 padding fits)
        respx.get(_UPNS_API).respond(200, content="00" * 32)
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_json_without_a_source(self) -> None:
        plain = json.dumps({"title": "n8wux6", "source": ""})
        respx.get(_UPNS_API).respond(200, content=_encrypt(plain))
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_a_source_the_cdn_denies_is_dead(self) -> None:
        respx.get(_UPNS_API).respond(200, content=_answer())
        respx.head(_HLS_URL).respond(403)
        async with httpx.AsyncClient() as client:
            assert await UpnsResolver(http_client=client).resolve(_UPNS_URL) is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_invalid_url_makes_no_request(self) -> None:
        api = respx.get(_UPNS_API).respond(200, content=_answer())
        async with httpx.AsyncClient() as client:
            resolver = UpnsResolver(http_client=client)
            assert await resolver.resolve("https://moflix.upns.xyz/") is None
            assert await resolver.resolve("https://example.com/#n8wux6") is None
        assert not api.called
