"""HttpxPluginBase adopts a permanent move of its site to another host."""

from __future__ import annotations

import httpx
import respx

from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase


class _Plugin(HttpxPluginBase):
    name = "move-test"
    provides = "stream"
    _domains = ["old.example"]


def _plugin(client: httpx.AsyncClient) -> _Plugin:
    plugin = _Plugin()
    plugin._client = client
    return plugin


class TestSiteMove:
    """hdfilme answered every search with a 301 from hdfilme.cafe to
    hdfilme.ceo: one more round trip per request, every request."""

    @respx.mock
    async def test_permanent_move_becomes_the_base_url(self) -> None:
        respx.get("https://old.example/", params={"story": "x"}).respond(
            301, headers={"Location": "https://new.example/?story=x"}
        )
        respx.get("https://new.example/", params={"story": "x"}).respond(
            200, text="hits"
        )

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            html = await plugin._fetch_text(plugin.base_url, params={"story": "x"})

        assert html == "hits"
        assert plugin.base_url == "https://new.example"

    @respx.mock
    async def test_safe_fetch_follows_the_move_too(self) -> None:
        respx.get("https://old.example/api").respond(
            308, headers={"Location": "https://new.example:8443/api"}
        )
        respx.get("https://new.example:8443/api").respond(200, json={})

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            await plugin._safe_fetch("https://old.example/api")

        assert plugin.base_url == "https://new.example:8443"

    @respx.mock
    async def test_temporary_redirects_are_not_a_move(self) -> None:
        respx.get("https://old.example/a").respond(
            302, headers={"Location": "https://new.example/a"}
        )
        respx.get("https://new.example/a").respond(200, text="ok")

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            await plugin._fetch_text("https://old.example/a")

        assert plugin.base_url == "https://old.example"

    @respx.mock
    async def test_same_host_and_foreign_redirects_keep_the_base(self) -> None:
        respx.get("https://old.example/a").respond(
            301, headers={"Location": "https://old.example/b"}
        )
        respx.get("https://old.example/b").respond(200, text="ok")
        respx.get("https://cdn.example/x").respond(
            301, headers={"Location": "https://other.example/x"}
        )
        respx.get("https://other.example/x").respond(200, text="ok")

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            await plugin._fetch_text("https://old.example/a")
            await plugin._fetch_text("https://cdn.example/x")

        assert plugin.base_url == "https://old.example"

    @respx.mock
    async def test_a_move_to_an_error_page_is_not_adopted(self) -> None:
        respx.get("https://old.example/a").respond(
            301, headers={"Location": "https://parked.example/a"}
        )
        respx.get("https://parked.example/a").respond(404)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            await plugin._fetch_text("https://old.example/a")

        assert plugin.base_url == "https://old.example"

    @respx.mock
    async def test_a_failing_new_host_sends_the_plugin_back(self) -> None:
        """hdfilme moves on every few days (.legal, .press, .party, .bid,
        .cafe, .ceo), and the old host redirects to the newest one; a new
        host that died kept every search on it until a restart."""
        respx.get("https://old.example/a").respond(
            301, headers={"Location": "https://new.example/a"}
        )
        respx.get("https://new.example/a").respond(200, text="ok")
        respx.get("https://new.example/b").mock(side_effect=httpx.ConnectError("gone"))
        respx.get("https://cdn.example/x").mock(side_effect=httpx.ConnectError("x"))

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            await plugin._fetch_text("https://old.example/a")
            await plugin._fetch_text("https://cdn.example/x")
            assert plugin.base_url == "https://new.example"

            assert await plugin._fetch_text("https://new.example/b") is None

        assert plugin.base_url == "https://old.example"

    @respx.mock
    async def test_safe_fetch_goes_back_too(self) -> None:
        respx.get("https://old.example/api").respond(
            308, headers={"Location": "https://new.example/api"}
        )
        respx.get("https://new.example/api").mock(
            side_effect=[httpx.Response(200, json={}), httpx.ReadTimeout("slow")]
        )

        async with httpx.AsyncClient(follow_redirects=True) as client:
            plugin = _plugin(client)
            await plugin._safe_fetch("https://old.example/api")
            assert plugin.base_url == "https://new.example"

            assert await plugin._safe_fetch("https://new.example/api") is None

        assert plugin.base_url == "https://old.example"
