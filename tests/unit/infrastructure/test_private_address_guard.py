"""Tests for the private-address guard of the shared HTTP client (SSRF)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpcore
import httpx
import pytest

import scavengarr.infrastructure.common.private_address_guard as guard_module
from scavengarr.infrastructure.common.private_address_guard import (
    GuardedNetworkBackend,
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


class _RecordingBackend(httpcore.AsyncNetworkBackend):
    """Inner backend that records where it was asked to connect."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.connected: list[str] = []
        self._refuse = refuse

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected.append(host)
        if host in self._refuse:
            raise httpcore.ConnectError(f"refused by {host}")
        return httpcore.AsyncMockStream([])

    async def sleep(self, seconds: float) -> None:
        return None


def _resolver(monkeypatch: pytest.MonkeyPatch, answers: list[list[str]]) -> list[str]:
    """getaddrinfo answering *answers* in turn; returns the hosts looked up."""
    lookups: list[str] = []

    async def _getaddrinfo(host: str, *args: object, **kwargs: object) -> list:
        lookups.append(host)
        family = {4: socket.AF_INET, 6: socket.AF_INET6}
        return [
            (
                family[ipaddress.ip_address(ip).version],
                socket.SOCK_STREAM,
                6,
                "",
                (ip, 0),
            )
            for ip in answers[min(len(lookups), len(answers)) - 1]
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _getaddrinfo)
    return lookups


class TestGuardedConnections:
    """The guard and httpcore resolved a name separately: a hostile DNS server
    could answer the guard with a public address and the connection with a
    LAN one (DNS rebinding). Connections now go to the checked addresses."""

    @pytest.mark.asyncio
    async def test_connects_to_the_checked_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lookups = _resolver(monkeypatch, [["93.184.215.14"]])
        guard = PrivateAddressGuard()
        inner = _RecordingBackend()

        await guard(_request("https://voe.sx/e/abc"))
        await GuardedNetworkBackend(guard, inner).connect_tcp("voe.sx", 443)

        assert inner.connected == ["93.184.215.14"]
        assert lookups == ["voe.sx"]

    @pytest.mark.asyncio
    async def test_rebinding_to_the_lan_is_refused_at_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _resolver(monkeypatch, [["93.184.215.14"], ["192.168.88.1"]])
        guard = PrivateAddressGuard()
        inner = _RecordingBackend()
        await guard(_request("https://evil.example/x"))
        monkeypatch.setattr(guard_module, "_DNS_CACHE_TTL_S", 0.0)
        guard._dns_cache.clear()  # noqa: SLF001  # the cached answer expired

        with pytest.raises(PrivateAddressError):
            await GuardedNetworkBackend(guard, inner).connect_tcp("evil.example", 443)

        assert inner.connected == []

    @pytest.mark.asyncio
    async def test_private_literal_is_refused_at_connect(self) -> None:
        inner = _RecordingBackend()

        with pytest.raises(PrivateAddressError):
            await GuardedNetworkBackend(PrivateAddressGuard(), inner).connect_tcp(
                "192.168.88.1", 80
            )

        assert inner.connected == []

    @pytest.mark.asyncio
    async def test_ipv4_first_then_the_next_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _resolver(monkeypatch, [["2606:4700::6810:84e5", "104.16.132.229", "1.1.1.1"]])
        inner = _RecordingBackend(refuse=frozenset({"104.16.132.229"}))

        await GuardedNetworkBackend(PrivateAddressGuard(), inner).connect_tcp(
            "cdn.example", 443
        )

        assert inner.connected == ["104.16.132.229", "1.1.1.1"]

    @pytest.mark.asyncio
    async def test_unresolvable_name_is_a_connect_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail(*_args: object, **_kwargs: object) -> list:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _fail)

        with pytest.raises(httpcore.ConnectError):
            await GuardedNetworkBackend(
                PrivateAddressGuard(), _RecordingBackend()
            ).connect_tcp("gone.example", 443)

    @pytest.mark.asyncio
    async def test_allowed_internal_host_connects_by_name(self) -> None:
        inner = _RecordingBackend()
        guard = PrivateAddressGuard(allowed_hosts=frozenset({"byparr"}))

        await GuardedNetworkBackend(guard, inner).connect_tcp("byparr", 8191)

        assert inner.connected == ["byparr"]
