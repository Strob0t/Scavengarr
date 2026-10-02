"""Tests for the private-address guard of the shared HTTP client (SSRF)."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from scavengarr.infrastructure.common.private_address_guard import (
    PrivateAddressError,
    PrivateAddressGuard,
)


def _request(url: str) -> httpx.Request:
    return httpx.Request("GET", url)


class TestLiteralAddresses:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.88.1/cgi-bin/reboot",
            "http://10.0.0.5/",
            "http://172.16.0.1/",
            "http://127.0.0.1:7979/api/v1/healthz",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/",  # carrier-grade NAT
            "http://[::ffff:192.168.1.1]/",
            "http://0.0.0.0/",
        ],
    )
    async def test_non_public_targets_are_refused(self, url: str) -> None:
        with pytest.raises(PrivateAddressError):
            await PrivateAddressGuard()(_request(url))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url", ["https://1.1.1.1/", "https://[2606:4700:4700::1111]/"]
    )
    async def test_public_targets_pass(self, url: str) -> None:
        await PrivateAddressGuard()(_request(url))


class TestHostnames:
    @pytest.mark.asyncio
    async def test_name_resolving_into_the_lan_is_refused(self) -> None:
        with pytest.raises(PrivateAddressError):
            await PrivateAddressGuard()(_request("http://localhost:7979/"))

    @pytest.mark.asyncio
    async def test_public_name_passes_and_is_resolved_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lookups: list[str] = []

        async def _getaddrinfo(host: str, *args: object, **kwargs: object) -> list:
            lookups.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _getaddrinfo)
        guard = PrivateAddressGuard()

        await guard(_request("https://voe.sx/e/abc"))
        await guard(_request("https://voe.sx/e/def"))

        assert lookups == ["voe.sx"]

    @pytest.mark.asyncio
    async def test_allowed_internal_host_passes(self) -> None:
        # The Byparr sidecar lives on the Docker network
        guard = PrivateAddressGuard(allowed_hosts=frozenset({"byparr"}))

        await guard(_request("http://byparr:8191/v1"))


class TestRedirects:
    @pytest.mark.asyncio
    async def test_redirect_into_the_lan_is_refused(self) -> None:
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [PrivateAddressGuard()]},
        ) as client:
            with pytest.raises(PrivateAddressError):
                await client.get("http://93.184.215.14/dl", follow_redirects=True)

        assert requested == ["http://93.184.215.14/dl"]
