"""Tests for the FlareSolverr/Byparr fetcher and the fetcher chain."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.domain.ports.browser_fetcher import (
    BrowserFetcherPort,
    BrowserSession,
    ClickThrough,
)
from scavengarr.infrastructure.browser.solver_fetcher import (
    ChainedBrowserFetcher,
    SolverFetcher,
)

_SOLVER = "http://byparr:8191"


def _solution(**overrides: object) -> dict[str, object]:
    solution: dict[str, object] = {
        "url": "https://site.example/page",
        "status": 200,
        "response": "<html><body>ok</body></html>",
        "cookies": [{"name": "cf_clearance", "value": "secret"}],
        "userAgent": "Mozilla/5.0",
    }
    solution.update(overrides)
    return {"status": "ok", "message": "Challenge solved!", "solution": solution}


class TestSolverFetcher:
    def test_implements_port(self) -> None:
        assert isinstance(
            SolverFetcher(http_client=AsyncMock(), base_url=_SOLVER),
            BrowserFetcherPort,
        )

    @respx.mock
    async def test_fetch_text_returns_the_solved_page(self) -> None:
        route = respx.post(f"{_SOLVER}/v1").respond(200, json=_solution())

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER + "/")
            body = await fetcher.fetch_text("https://site.example/page", timeout=40)

        assert body == "<html><body>ok</body></html>"
        request = json.loads(route.calls[0].request.content)
        assert request == {
            "cmd": "request.get",
            "url": "https://site.example/page",
            "maxTimeout": 40_000,
        }

    @respx.mock
    async def test_json_rendered_by_the_browser_is_unwrapped(self) -> None:
        page = '<html><head></head><body><pre>{"a": 1}</pre></body></html>'
        respx.post(f"{_SOLVER}/v1").respond(200, json=_solution(response=page))

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER)
            body = await fetcher.fetch_text("https://site.example/api", timeout=40)

        assert body == '{"a": 1}'

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "error", "message": "Challenge not solved"},
            _solution(status=404),
            {"status": "ok"},
        ],
    )
    @respx.mock
    async def test_failures_return_none(self, payload: dict[str, object]) -> None:
        respx.post(f"{_SOLVER}/v1").respond(200, json=payload)

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER)
            assert await fetcher.fetch_text("https://site.example/x", timeout=5) is None

    @respx.mock
    async def test_unreachable_solver_returns_none(self) -> None:
        respx.post(f"{_SOLVER}/v1").mock(side_effect=httpx.ConnectError("down"))

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER)
            assert await fetcher.fetch_text("https://site.example/x", timeout=5) is None

    @respx.mock
    async def test_resolve_redirect_returns_offsite_url(self) -> None:
        respx.post(f"{_SOLVER}/v1").respond(
            200, json=_solution(url="https://filecrypt.cc/Container/X.html")
        )

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER)
            target = await fetcher.resolve_redirect(
                "https://site.example/external/abc", timeout=40
            )

        assert target == "https://filecrypt.cc/Container/X.html"

    @respx.mock
    async def test_session_of_the_last_solution(self) -> None:
        respx.post(f"{_SOLVER}/v1").respond(200, json=_solution())

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER)
            await fetcher.fetch_text("https://site.example/page", timeout=40)
            session = await fetcher.session("https://site.example/other")

        assert session == BrowserSession(
            cookies={"cf_clearance": "secret"}, user_agent="Mozilla/5.0"
        )
        assert await fetcher.session("https://elsewhere.example/") is None

    @respx.mock
    async def test_resolve_redirect_same_host_returns_none(self) -> None:
        respx.post(f"{_SOLVER}/v1").respond(
            200, json=_solution(url="https://site.example/external/abc")
        )

        async with httpx.AsyncClient() as client:
            fetcher = SolverFetcher(http_client=client, base_url=_SOLVER)
            assert (
                await fetcher.resolve_redirect(
                    "https://site.example/external/abc", timeout=40
                )
                is None
            )


class TestChainedBrowserFetcher:
    async def test_first_success_wins(self) -> None:
        first, second = AsyncMock(), AsyncMock()
        first.fetch_text.return_value = "one"
        chain = ChainedBrowserFetcher([first, second])

        assert await chain.fetch_text("https://x/", timeout=10) == "one"
        second.fetch_text.assert_not_awaited()

    async def test_falls_through_on_none(self) -> None:
        first, second = AsyncMock(), AsyncMock()
        first.fetch_text.return_value = None
        second.fetch_text.return_value = "two"
        first.resolve_redirect.return_value = None
        second.resolve_redirect.return_value = "https://t/"
        chain = ChainedBrowserFetcher([first, second])

        assert await chain.fetch_text("https://x/", timeout=10) == "two"
        assert await chain.resolve_redirect("https://x/", timeout=10) == "https://t/"

    async def test_session_comes_from_the_fetcher_that_answered(self) -> None:
        """The browser that solved the page holds the site's session."""
        first, second = AsyncMock(), AsyncMock()
        first.fetch_text.return_value = None
        second.fetch_text.return_value = "two"
        session = BrowserSession(cookies={"a": "1"}, user_agent="UA")
        second.session.return_value = session
        chain = ChainedBrowserFetcher([first, second])

        await chain.fetch_text("https://x.example/page", timeout=10)

        assert await chain.session("https://x.example/next") == session
        first.session.assert_not_awaited()
        assert await chain.session("https://unknown.example/") is None

    async def test_all_fail_returns_none(self) -> None:
        only = AsyncMock()
        only.fetch_text.return_value = None
        assert await ChainedBrowserFetcher([only]).fetch_text("u", timeout=1) is None

    async def test_click_through_falls_through_on_none(self) -> None:
        first, second = AsyncMock(), AsyncMock()
        first.click_through.return_value = None
        second.click_through.return_value = ClickThrough(url="https://voe.sx/e/a")
        chain = ChainedBrowserFetcher([first, second])

        result = await chain.click_through("https://s.to/ep", "button", timeout=5)

        assert result == ClickThrough(url="https://voe.sx/e/a")
        second.click_through.assert_awaited_once_with(
            "https://s.to/ep", "button", timeout=5
        )


async def test_solver_cannot_click() -> None:
    """FlareSolverr only loads URLs; clicking is left to the own browser."""
    solver = SolverFetcher(http_client=AsyncMock(), base_url=_SOLVER)

    assert await solver.click_through("https://s.to/ep", "button", timeout=5) is None
