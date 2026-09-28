"""Tests for HttpxPluginBase._fetch_text() and its browser fallback."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

_CF_CHALLENGE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body><div id='challenge-platform'></div></body></html>"
)


class _Plugin(HttpxPluginBase):
    name = "cf-test"
    provides = "download"
    _domains = ["cf.example"]


@pytest.fixture(autouse=True)
def _reset_fetcher() -> Iterator[None]:
    yield
    HttpxPluginBase.set_browser_fetcher(None)


async def _plugin_with_client(client: httpx.AsyncClient) -> _Plugin:
    plugin = _Plugin()
    plugin._client = client
    return plugin


class TestFetchText:
    @respx.mock
    async def test_returns_body_on_success(self) -> None:
        respx.get("https://cf.example/page").respond(200, text="<html>ok</html>")
        fetcher = AsyncMock()
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            text = await plugin._fetch_text("https://cf.example/page")

        assert text == "<html>ok</html>"
        fetcher.fetch_text.assert_not_awaited()

    @respx.mock
    async def test_passes_query_params(self) -> None:
        route = respx.get("https://cf.example/api", params={"q": "iron man"}).respond(
            200, text="{}"
        )

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            text = await plugin._fetch_text(
                "https://cf.example/api", params={"q": "iron man"}
            )

        assert text == "{}"
        assert route.called

    @respx.mock
    async def test_cloudflare_challenge_falls_back_to_browser(self) -> None:
        respx.get("https://cf.example/api").respond(403, text=_CF_CHALLENGE)
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value='{"result": [1]}')
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            text = await plugin._fetch_text("https://cf.example/api", params={"q": "x"})

        assert text == '{"result": [1]}'
        url = fetcher.fetch_text.await_args.args[0]
        assert url == "https://cf.example/api?q=x"  # query string preserved

    @respx.mock
    async def test_challenge_without_fetcher_returns_none(self) -> None:
        respx.get("https://cf.example/page").respond(403, text=_CF_CHALLENGE)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            assert await plugin._fetch_text("https://cf.example/page") is None

    @respx.mock
    async def test_plain_error_does_not_use_browser(self) -> None:
        respx.get("https://cf.example/missing").respond(404, text="not found")
        fetcher = AsyncMock()
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            assert await plugin._fetch_text("https://cf.example/missing") is None

        fetcher.fetch_text.assert_not_awaited()

    @respx.mock
    async def test_network_error_returns_none(self) -> None:
        respx.get("https://cf.example/page").mock(side_effect=httpx.ConnectError("x"))

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            assert await plugin._fetch_text("https://cf.example/page") is None

    @respx.mock
    async def test_browser_failure_returns_none(self) -> None:
        respx.get("https://cf.example/page").respond(403, text=_CF_CHALLENGE)
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value=None)
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            assert await plugin._fetch_text("https://cf.example/page") is None
