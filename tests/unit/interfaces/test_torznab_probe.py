"""Tests for the Torznab reachability probe (HEAD, GET fallback)."""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.interfaces.api.torznab.router import _lightweight_http_probe


class TestLightweightHttpProbe:
    @respx.mock
    @pytest.mark.asyncio()
    async def test_head_answer(self) -> None:
        respx.head("https://example.com/").respond(200)

        async with httpx.AsyncClient() as client:
            result = await _lightweight_http_probe(
                client, base_url="https://example.com/search?q=x"
            )

        assert result == (True, 200, None, "https://example.com/")

    @respx.mock
    @pytest.mark.asyncio()
    async def test_get_fallback_when_head_is_not_allowed(self) -> None:
        # The fallback passed timeout= to AsyncClient.send(), which has no
        # such parameter: a real client raised TypeError (mocks hid it)
        respx.head("https://example.com/").respond(405)
        get = respx.get("https://example.com/").respond(206, content=b"x")

        async with httpx.AsyncClient() as client:
            result = await _lightweight_http_probe(
                client, base_url="https://example.com/", timeout_seconds=2.0
            )

        assert result == (True, 206, None, "https://example.com/")
        assert get.calls.last.request.headers["Range"] == "bytes=0-0"
