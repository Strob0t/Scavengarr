"""Tests for building the shared HTTP client in composition."""

from __future__ import annotations

import httpx
import pytest

from scavengarr.infrastructure.common.private_address_guard import (
    PrivateAddressError,
    PrivateAddressGuard,
)
from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.interfaces.composition import build_http_client


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
