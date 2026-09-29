"""Tests for HttpxPluginBase._fetch_text() and its browser fallback."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
import structlog.testing

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
    HttpxPluginBase._cf_blocked_until.clear()
    yield
    HttpxPluginBase.set_browser_fetcher(None)
    HttpxPluginBase._cf_blocked_until.clear()


async def _plugin_with_client(client: httpx.AsyncClient) -> _Plugin:
    plugin = _Plugin()
    plugin._client = client
    return plugin


class TestResolveRedirect:
    @respx.mock
    async def test_returns_offsite_location(self) -> None:
        respx.get("https://cf.example/external/abc").respond(
            302, headers={"Location": "https://filecrypt.cc/Container/x.html"}
        )

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            target = await plugin._resolve_redirect("https://cf.example/external/abc")

        assert target == "https://filecrypt.cc/Container/x.html"

    @respx.mock
    async def test_follows_same_host_hops(self) -> None:
        respx.get("https://cf.example/external/abc").respond(
            302, headers={"Location": "/go/abc"}
        )
        respx.get("https://cf.example/go/abc").respond(
            301, headers={"Location": "https://rapidgator.net/file/1"}
        )

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            target = await plugin._resolve_redirect("https://cf.example/external/abc")

        assert target == "https://rapidgator.net/file/1"

    @respx.mock
    async def test_challenge_uses_browser(self) -> None:
        respx.get("https://cf.example/external/abc").respond(403, text=_CF_CHALLENGE)
        fetcher = AsyncMock()
        fetcher.resolve_redirect = AsyncMock(
            return_value="https://filecrypt.cc/Container/y.html"
        )
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            target = await plugin._resolve_redirect("https://cf.example/external/abc")

        assert target == "https://filecrypt.cc/Container/y.html"
        fetcher.resolve_redirect.assert_awaited_once()
        assert fetcher.resolve_redirect.await_args.args[0] == (
            "https://cf.example/external/abc"
        )

    @respx.mock
    async def test_no_redirect_returns_none(self) -> None:
        respx.get("https://cf.example/external/abc").respond(200, text="page")

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            assert (
                await plugin._resolve_redirect("https://cf.example/external/abc")
                is None
            )


class TestCloudflareHostMemo:
    """After one challenge, the host goes straight to the browser.

    The doomed httpx request would otherwise count against the site's rate
    limit (filmfans answers bursts with 429).
    """

    @respx.mock
    async def test_second_request_skips_httpx(self) -> None:
        route = respx.get(url__startswith="https://cf.example/").respond(
            403, text=_CF_CHALLENGE
        )
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value="<html>ok</html>")
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            await plugin._fetch_text("https://cf.example/a")
            await plugin._fetch_text("https://cf.example/b", params={"q": "x"})

        assert route.call_count == 1
        urls = [c.args[0] for c in fetcher.fetch_text.await_args_list]
        assert urls == ["https://cf.example/a", "https://cf.example/b?q=x"]

    @respx.mock
    async def test_memo_also_covers_redirects(self) -> None:
        route = respx.get(url__startswith="https://cf.example/").respond(
            403, text=_CF_CHALLENGE
        )
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value="<html>ok</html>")
        fetcher.resolve_redirect = AsyncMock(return_value="https://filecrypt.cc/c")
        HttpxPluginBase.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            await plugin._fetch_text("https://cf.example/a")
            target = await plugin._resolve_redirect("https://cf.example/external/x")

        assert target == "https://filecrypt.cc/c"
        assert route.call_count == 1

    @respx.mock
    async def test_memo_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        route = respx.get(url__startswith="https://cf.example/").respond(
            403, text=_CF_CHALLENGE
        )
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value="<html>ok</html>")
        HttpxPluginBase.set_browser_fetcher(fetcher)
        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "scavengarr.infrastructure.plugins.httpx_base.time.monotonic",
            lambda: clock["now"],
        )

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            await plugin._fetch_text("https://cf.example/a")
            clock["now"] += 31 * 60
            await plugin._fetch_text("https://cf.example/b")

        assert route.call_count == 2


class TestResolveOwnLinks:
    async def test_resolves_own_host_and_keeps_others(self) -> None:
        plugin = _Plugin()

        async def _resolve(url: str, *, context: str = "") -> str | None:
            return {
                "https://cf.example/external/a": "https://filecrypt.cc/Container/a",
            }.get(url)

        plugin._resolve_redirect = _resolve  # type: ignore[method-assign]
        links = [
            {"hoster": "rapidgator", "link": "https://cf.example/external/a"},
            {"hoster": "ddownload", "link": "https://cf.example/external/dead"},
            {"hoster": "nitroflare", "link": "https://nitroflare.com/view/x"},
        ]

        resolved = await plugin._resolve_own_links(links)

        assert resolved == [
            {"hoster": "rapidgator", "link": "https://filecrypt.cc/Container/a"},
            {"hoster": "nitroflare", "link": "https://nitroflare.com/view/x"},
        ]


class TestParseJsonText:
    def test_valid_object(self) -> None:
        assert _Plugin()._parse_json_text('{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("body", [None, "not json", "[1, 2]"])
    def test_invalid_returns_none(self, body: str | None) -> None:
        assert _Plugin()._parse_json_text(body) is None


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
    async def test_error_logs_the_challenge_kind(self) -> None:
        respx.get("https://cf.example/page").respond(
            403, text="<title>DDoS-Guard</title>", headers={"server": "ddos-guard"}
        )

        async with httpx.AsyncClient() as client:
            plugin = await _plugin_with_client(client)
            with structlog.testing.capture_logs() as logs:
                assert await plugin._fetch_text("https://cf.example/page") is None

        errors = [e for e in logs if e["event"] == "cf-test_http_error"]
        assert errors[0]["challenge"] == "ddos_guard"

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
