"""Tests for building the shared HTTP client in composition."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from scavengarr.infrastructure.common.private_address_guard import (
    PrivateAddressError,
    PrivateAddressGuard,
)
from scavengarr.infrastructure.common.retry_transport import RetryTransport
from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.interfaces.composition import build_http_client


def _pool(client: httpx.AsyncClient) -> Any:
    """The connection pool behind the shared client's retry transport."""
    transport = client._transport  # noqa: SLF001
    assert isinstance(transport, RetryTransport)
    return transport._wrapped._pool  # noqa: SLF001  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_connections_stay_open_between_requests() -> None:
    """A stream request talks to 18-42 hosts; with httpx's 5 s keep-alive
    every pause between two requests closed them all, and the next request
    paid TLS handshakes again (21% of the Python CPU on the Pi, 2026-10-04)."""
    client = build_http_client(AppConfig())
    try:
        pool = _pool(client)
        assert pool._keepalive_expiry == 60.0  # noqa: SLF001
        assert pool._max_keepalive_connections == 100  # noqa: SLF001
        assert pool._http2 is False  # noqa: SLF001
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_http2_is_a_switch() -> None:
    client = build_http_client(AppConfig(http_http2=True))
    try:
        assert _pool(client)._http2 is True  # noqa: SLF001
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_a_dead_host_fails_at_connect() -> None:
    """A host that does not answer costs the connect timeout, not the full
    read timeout."""
    client = build_http_client(AppConfig(http_timeout_seconds=15.0))
    try:
        assert client.timeout.connect == 5.0
        assert client.timeout.read == 15.0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_shared_client_refuses_the_lan_but_reaches_the_solver() -> None:
    client = build_http_client(AppConfig(playwright_solver_url="http://byparr:8191"))
    try:
        guards = [
            hook
            for hook in client.event_hooks["request"]
            if isinstance(hook, PrivateAddressGuard)
        ]
        assert len(guards) == 1
        await guards[0](httpx.Request("POST", "http://byparr:8191/v1"))
        with pytest.raises(PrivateAddressError):
            await guards[0](httpx.Request("GET", "http://192.168.88.1/"))
    finally:
        await client.aclose()
