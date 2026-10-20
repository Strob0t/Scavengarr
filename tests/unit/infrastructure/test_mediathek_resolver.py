"""Tests for MediathekResolver (the public broadcasters' own files)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers.mediathek import MediathekResolver
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)

_MP4 = "https://nrodlzdf-a.akamaihd.net/de/zdf/26/10/sendung_6660k_p37v17.mp4"
_HLS = "https://srf-vod-amd.akamaized.net/ch/hls/sendung/index-f6-v1-a1.m3u8"
_KIKA = "https://kika-progressive.ard-mcdn.de/videos/folge_1920x1080-50p-5000kbit.mp4"


def _resolver(client: httpx.AsyncClient) -> MediathekResolver:
    return MediathekResolver(http_client=client)


class TestClaims:
    def test_name(self) -> None:
        assert _resolver(httpx.AsyncClient()).name == "mediathek"

    @pytest.mark.parametrize(
        "host",
        [
            "nrodlzdf-a.akamaihd.net",
            "rodl.zdf.de",
            "arteptweb-a.akamaihd.net",
            "cdn-storage.br.de",
            "srf-vod-amd.akamaized.net",
            "apasfiis.sf.apa.at",
            "ctv-videos.daserste.de",
        ],
    )
    def test_the_broadcasters_hosts_are_claimed(self, host: str) -> None:
        assert host in _resolver(httpx.AsyncClient()).supported_hosts

    def test_the_ard_cdn_is_claimed_whole(self) -> None:
        # hr-, kika-, mdr-, ndr-, wdr-progressive.ard-mcdn.de and the others
        assert _resolver(httpx.AsyncClient()).supported_domains == frozenset(
            {"ard-mcdn"}
        )

    def test_no_shared_akamai_zone_is_claimed_whole(self) -> None:
        # akamaihd.net and akamaized.net serve other hosters too
        resolver = _resolver(httpx.AsyncClient())
        assert {"akamaihd", "akamaized"}.isdisjoint(resolver.supported_domains)


class TestResolve:
    @respx.mock
    async def test_a_file_that_answers_as_video_is_the_stream(self) -> None:
        respx.head(_MP4).respond(200, headers={"content-type": "video/mp4"})

        async with httpx.AsyncClient() as client:
            result = await _resolver(client).resolve(_MP4)

        assert result is not None
        assert result.video_url == _MP4
        assert result.is_hls is False
        assert result.headers == {}
        assert result.address_bound is False

    @respx.mock
    async def test_a_playlist_is_hls(self) -> None:
        respx.head(_HLS).respond(200, headers={"content-type": "application/x-mpegURL"})

        async with httpx.AsyncClient() as client:
            result = await _resolver(client).resolve(_HLS)

        assert result is not None
        assert result.is_hls is True

    @respx.mock
    async def test_a_file_without_a_video_type_plays_too(self) -> None:
        # KiKA's files come as application/octet-stream
        respx.head(_KIKA).respond(
            200, headers={"content-type": "application/octet-stream"}
        )

        async with httpx.AsyncClient() as client:
            result = await _resolver(client).resolve(_KIKA)

        assert result is not None
        assert result.video_url == _KIKA

    @pytest.mark.parametrize(
        ("status", "content_type"),
        [
            (404, "text/html"),
            # A geo-blocked file (ORF, SRF abroad)
            (403, "text/html"),
            # An error page answering 200
            (200, "text/html; charset=utf-8"),
        ],
    )
    @respx.mock
    async def test_a_file_that_does_not_answer_as_media_is_none(
        self, status: int, content_type: str
    ) -> None:
        respx.head(_MP4).respond(status, headers={"content-type": content_type})

        async with httpx.AsyncClient() as client:
            assert await _resolver(client).resolve(_MP4) is None

    @respx.mock
    async def test_a_network_error_is_none(self) -> None:
        respx.head(_MP4).mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            assert await _resolver(client).resolve(_MP4) is None


class TestRegistry:
    @respx.mock
    async def test_a_claimed_host_goes_to_the_resolver(self) -> None:
        respx.head(_MP4).respond(200, headers={"content-type": "video/mp4"})

        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_resolver(client)], http_client=client
            )
            result = await registry.resolve(_MP4, hoster="mediathek")

        assert result is not None
        assert result.video_url == _MP4
        assert registry.unresolved_hosts() == {}

    @respx.mock
    async def test_a_host_of_the_ard_cdn_goes_to_the_resolver(self) -> None:
        respx.head(_KIKA).respond(
            200, headers={"content-type": "application/octet-stream"}
        )

        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(
                resolvers=[_resolver(client)], http_client=client
            )
            result = await registry.resolve(_KIKA, hoster="mediathek")

        assert result is not None
        assert registry.unresolved_hosts() == {}

    def test_the_plugins_hoster_label_is_the_resolver(self) -> None:
        registry = HosterResolverRegistry(resolvers=[_resolver(httpx.AsyncClient())])

        assert registry.canonical_hoster("mediathek") == "mediathek"
        assert registry.canonical_hoster("nrodlzdf-a.akamaihd.net") == "mediathek"
