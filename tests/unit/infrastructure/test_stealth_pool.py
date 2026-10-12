"""Tests for StealthPool — Patchright browser pool."""

from __future__ import annotations

import asyncio
import contextvars
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import structlog
from patchright.async_api import Error as PlaywrightError

from scavengarr.domain.ports.browser_fetcher import (
    BrowserSession,
    ClickThrough,
    PageClaim,
    Redirect,
    page_claim,
)
from scavengarr.infrastructure.browser.hardening import (
    BLOCKED_RESOURCE_TYPES,
    block_heavy_resources,
)
from scavengarr.infrastructure.browser.page_gate import (
    ChallengeMark,
    PageBusy,
    PageGate,
    challenge_mark,
    work_clock,
)
from scavengarr.infrastructure.browser.stealth_pool import StealthPool

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _mock_pool_stack() -> tuple[MagicMock, AsyncMock, AsyncMock]:
    """Return (shared browser pool, browser, context) mocks wired together."""
    context = AsyncMock()
    context.new_page = AsyncMock()
    context.route = AsyncMock()
    context.close = AsyncMock()

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.is_connected = MagicMock(return_value=True)

    shared_pool = MagicMock()
    shared_pool.warmup = AsyncMock(return_value=(browser, MagicMock()))

    return shared_pool, browser, context


def _mock_page(
    *,
    html: str = "<html><body>Player</body></html>",
    title: str = "Watch Video",
) -> AsyncMock:
    """Create a mock Page with goto, content, title, close."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.content = AsyncMock(return_value=html)
    page.title = AsyncMock(return_value=title)
    page.wait_for_function = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    page.close = AsyncMock()
    return page


# ------------------------------------------------------------------
# block_heavy_resources
# ------------------------------------------------------------------


class TestBlockResources:
    """Route handler blocks heavy resource types."""

    @pytest.mark.parametrize("rtype", sorted(BLOCKED_RESOURCE_TYPES))
    async def test_blocks_heavy_resource(self, rtype: str) -> None:
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = rtype
        await block_heavy_resources(route)
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.anime-loads.org/.well-known/ddos-guard/mark/?data=x",
            "https://check.ddos-guard.net/set/id/abc",
        ],
    )
    async def test_lets_ddos_guards_check_images_through(self, url: str) -> None:
        """DDoS-Guard's check sets its cookies through image beacons:
        blocked, the challenge reloaded until it timed out (animeloads, 0 of
        3; code review, 2026-10-06)."""
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = "image"
        route.request.url = url

        await block_heavy_resources(route)

        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()

    @pytest.mark.parametrize("rtype", ["document", "script", "xhr", "fetch"])
    async def test_allows_essential_resources(self, rtype: str) -> None:
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = rtype
        await block_heavy_resources(route)
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()


# ------------------------------------------------------------------
# StealthPool lifecycle
# ------------------------------------------------------------------


class TestStealthPoolLifecycle:
    """Context on the shared browser, resource blocking, cleanup."""

    async def test_ensure_context_uses_shared_browser(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        pool = StealthPool(browser_pool=shared_pool, timeout_ms=10_000)
        ctx = await pool._ensure_context()

        assert ctx is context
        shared_pool.warmup.assert_awaited_once()
        browser.new_context.assert_awaited_once_with(service_workers="block")
        context.route.assert_awaited_once_with("**/*", block_heavy_resources)

    async def test_ensure_context_reuses_existing(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        pool = StealthPool(browser_pool=shared_pool)
        ctx1 = await pool._ensure_context()
        ctx2 = await pool._ensure_context()

        assert ctx1 is ctx2
        shared_pool.warmup.assert_awaited_once()

    async def test_recreates_context_after_browser_relaunch(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)
        await pool._ensure_context()

        browser.is_connected = MagicMock(return_value=False)
        new_context = AsyncMock()
        new_browser = AsyncMock()
        new_browser.new_context = AsyncMock(return_value=new_context)
        new_browser.is_connected = MagicMock(return_value=True)
        shared_pool.warmup = AsyncMock(return_value=(new_browser, MagicMock()))

        assert await pool._ensure_context() is new_context

    async def test_cleanup_closes_only_context(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        pool = StealthPool(browser_pool=shared_pool)
        await pool._ensure_context()
        await pool.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_not_awaited()
        assert pool._context is None

    async def test_cleanup_noop_when_not_started(self) -> None:
        shared_pool, _, _ = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)
        await pool.cleanup()  # no error


# ------------------------------------------------------------------
# StealthPool.fetch_text (BrowserFetcherPort)
# ------------------------------------------------------------------


def _fetch_page(
    *,
    status: int = 200,
    content_type: str = "text/html",
    html: str = "<html><body>real page</body></html>",
    title: str = "Real Page",
    fetched: str | None = '{"result": []}',
) -> AsyncMock:
    page = _mock_page(html=html, title=title)
    page.goto = AsyncMock(return_value=MagicMock(status=status))

    async def _evaluate(script: str, *args: object) -> object:
        return content_type if "contentType" in script else fetched

    page.evaluate = AsyncMock(side_effect=_evaluate)
    return page


class TestStealthPoolClearanceStore:
    async def test_passed_page_is_remembered(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page()
        context.new_page = AsyncMock(return_value=page)
        store = AsyncMock()

        await StealthPool(browser_pool=shared_pool, clearance_store=store).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        store.remember.assert_awaited_once_with(page.context)

    async def test_unsolved_challenge_is_not_remembered(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(return_value=_fetch_page())
        store = AsyncMock()

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=False),
        ):
            await StealthPool(
                browser_pool=shared_pool, clearance_store=store
            ).fetch_text("https://filmfans.org/x", timeout=10)

        store.remember.assert_not_awaited()


class TestStealthPoolFetchText:
    async def test_is_a_browser_fetcher(self) -> None:
        from scavengarr.domain.ports import BrowserFetcherPort

        shared_pool, _, _ = _mock_pool_stack()
        assert isinstance(StealthPool(browser_pool=shared_pool), BrowserFetcherPort)

    async def test_returns_html_for_html_pages(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page()
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        assert text == "<html><body>real page</body></html>"
        page.close.assert_awaited_once()

    async def test_pages_count_toward_a_browser_restart(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(return_value=_fetch_page())

        await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        shared_pool.note_page.assert_called_once_with()

    async def test_the_browser_is_held_from_opening_a_page_to_closing_it(
        self,
    ) -> None:
        """A restart when no context listed a page killed a page the other
        slot was still opening (code review, 2026-10-06)."""
        events: list[str] = []
        shared_pool, _, context = _mock_pool_stack()

        @asynccontextmanager
        async def _lease() -> AsyncIterator[None]:
            events.append("held")
            yield
            events.append("released")

        shared_pool.lease = _lease
        page = _fetch_page()

        async def _new_page() -> AsyncMock:
            events.append("opened")
            return page

        async def _close() -> None:
            events.append("closed")

        context.new_page = AsyncMock(side_effect=_new_page)
        page.close = AsyncMock(side_effect=_close)

        await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        assert events == ["held", "opened", "closed", "released"]

    async def test_returns_raw_body_for_non_html(self) -> None:
        """JSON is re-fetched in-page: raw text, not Chrome's JSON viewer."""
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(content_type="application/json")
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/api/v2/search?q=x", timeout=10
        )

        assert text == '{"result": []}'

    async def test_solves_challenge_before_reading(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(status=403, title="Just a moment...")
        context.new_page = AsyncMock(return_value=page)
        pool = StealthPool(browser_pool=shared_pool)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=True),
        ) as mock_solve:
            text = await pool.fetch_text("https://filmfans.org/x", timeout=10)

        assert text == "<html><body>real page</body></html>"
        mock_solve.assert_awaited_once_with(page, timeout_ms=10_000)

    async def test_unsolved_challenge_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(
            return_value=_fetch_page(status=403, title="Just a moment...")
        )
        pool = StealthPool(browser_pool=shared_pool)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=False),
        ):
            assert await pool.fetch_text("https://filmfans.org/x", timeout=10) is None

    async def test_http_error_without_challenge_returns_none(self) -> None:
        # The in-page fetch gets the same 404
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(
            return_value=_fetch_page(status=404, title="Not Found", fetched=None)
        )

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/missing", timeout=10
        )

        assert text is None

    async def test_api_refusing_a_page_load_is_asked_in_page(self) -> None:
        """moflix's API answers a request without its site's Referer with 401
        (Laravel Sanctum). Only a challenge's reload sends one; production's
        browser held a stored clearance, loaded the API directly and got 401
        on every search (2026-10-04). The site's pages call it with fetch()."""
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(status=401, content_type="application/json", title="")
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://moflix-stream.xyz/api/v1/search/x?query=x", timeout=10
        )

        assert text == '{"result": []}'
        page.close.assert_awaited_once()

    async def test_navigation_error_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page()
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_FAILED"))
        context.new_page = AsyncMock(return_value=page)

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/x", timeout=10
        )

        assert text is None
        page.close.assert_awaited_once()

    async def test_concurrency_is_bounded(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        active = {"now": 0, "max": 0}

        async def _goto(*_: object, **__: object) -> MagicMock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.01)
            active["now"] -= 1
            return MagicMock(status=200)

        def _new_page() -> AsyncMock:
            page = _fetch_page()
            page.goto = AsyncMock(side_effect=_goto)
            return page

        context.new_page = AsyncMock(side_effect=lambda: _new_page())
        pool = StealthPool(browser_pool=shared_pool, pages=PageGate(limit=2))

        await asyncio.gather(
            *(pool.fetch_text(f"https://x.org/{i}", timeout=10) for i in range(6))
        )

        assert active["max"] == 2

    async def test_rate_limit_is_retried_with_backoff(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(title="Too Many Requests")
        page.goto = AsyncMock(
            side_effect=[MagicMock(status=429), MagicMock(status=200)]
        )
        page.title = AsyncMock(side_effect=["Too Many Requests", "Real Page"])
        context.new_page = AsyncMock(return_value=page)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.asyncio.sleep",
            AsyncMock(),
        ) as mock_sleep:
            text = await StealthPool(browser_pool=shared_pool).fetch_text(
                "https://filmfans.org/x", timeout=10
            )

        assert text == "<html><body>real page</body></html>"
        mock_sleep.assert_awaited_once_with(2.0)

    async def test_rate_limit_gives_up_after_backoffs(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _fetch_page(title="Too Many Requests", fetched=None)
        page.goto = AsyncMock(return_value=MagicMock(status=429))
        context.new_page = AsyncMock(return_value=page)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.asyncio.sleep",
            AsyncMock(),
        ) as mock_sleep:
            text = await StealthPool(browser_pool=shared_pool).fetch_text(
                "https://filmfans.org/x", timeout=10
            )

        assert text is None
        assert [c.args[0] for c in mock_sleep.await_args_list] == [2.0, 5.0, 10.0]
        assert page.goto.await_count == 4


# ------------------------------------------------------------------
# StealthPool.resolve_redirect
# ------------------------------------------------------------------


def _request(
    url: str, *, navigation: bool = True, redirected_from: MagicMock | None = None
) -> MagicMock:
    request = MagicMock()
    request.url = url
    request.is_navigation_request = MagicMock(return_value=navigation)
    request.redirected_from = redirected_from
    return request


class TestStealthPoolSession:
    """The site's cookies in the stealth context plus the browser's User-Agent."""

    def _pool(self, cookies: list[dict[str, str]]) -> tuple[StealthPool, AsyncMock]:
        shared_pool, _, context = _mock_pool_stack()
        page = _mock_page()
        page.evaluate = AsyncMock(return_value="Mozilla/5.0 Chrome/153.0.0.0")
        context.new_page = AsyncMock(return_value=page)
        context.cookies = AsyncMock(return_value=cookies)
        return StealthPool(browser_pool=shared_pool), context

    async def test_returns_cookies_and_user_agent(self) -> None:
        pool, context = self._pool(
            [
                {"name": "ha-waf-ticket", "value": "t1"},
                {"name": "PHPSESSID", "value": "s1"},
            ]
        )

        session = await pool.session("https://kinoger.com/x")

        assert session == BrowserSession(
            cookies={"ha-waf-ticket": "t1", "PHPSESSID": "s1"},
            user_agent="Mozilla/5.0 Chrome/153.0.0.0",
        )
        context.cookies.assert_awaited_once_with("https://kinoger.com/x")

    async def test_user_agent_is_read_once(self) -> None:
        pool, context = self._pool([{"name": "a", "value": "1"}])

        await pool.session("https://kinoger.com/x")
        await pool.session("https://kinoger.com/y")

        context.new_page.assert_awaited_once()
        context.new_page.return_value.close.assert_awaited_once()

    async def test_without_cookies_there_is_no_session(self) -> None:
        pool, _ = self._pool([])

        assert await pool.session("https://kinoger.com/x") is None


def _redirect_page(hops: list[tuple[list[str], int]]) -> MagicMock:
    """Page whose goto() emits a "request" event per redirect hop.

    Each entry is (urls requested in order, final HTTP status). Playwright
    routes only see the first URL of a redirect chain, but "request" events
    fire for every hop, which is what resolve_redirect listens to.
    """
    page = _mock_page(title="FilmFans")
    listeners: list[object] = []

    def _on(event: str, callback: object) -> None:
        if event == "request":
            listeners.append(callback)

    calls = iter(hops)

    async def _goto(url: str, **_: object) -> MagicMock:
        urls, status = next(calls)
        for hop in urls:
            for callback in listeners:
                callback(_request(hop))  # type: ignore[operator]
        return MagicMock(status=status)

    page.on = MagicMock(side_effect=_on)
    page.goto = AsyncMock(side_effect=_goto)
    return page


class TestStealthPoolResolveRedirect:
    async def test_returns_first_offsite_hop(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page(
            [
                (
                    [
                        "https://filmfans.org/external/abc",
                        "https://filecrypt.cc/Container/2bdf64bab2.html?mirror=0",
                        "https://filecrypt.cc/404.html",
                    ],
                    404,  # dead container: still reported, validation decides
                )
            ]
        )
        context.new_page = AsyncMock(return_value=page)

        redirect = await StealthPool(browser_pool=shared_pool).resolve_redirect(
            "https://filmfans.org/external/abc", timeout=10
        )

        assert redirect == Redirect(
            "https://filecrypt.cc/Container/2bdf64bab2.html?mirror=0", passed=True
        )
        page.close.assert_awaited_once()

    async def test_same_host_only_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([(["https://filmfans.org/external/abc"], 200)])
        context.new_page = AsyncMock(return_value=page)
        pool = StealthPool(browser_pool=shared_pool)

        # The site's own page: its verdict on the link-out, no challenge open
        assert await pool.resolve_redirect(
            "https://filmfans.org/external/abc", timeout=10
        ) == Redirect(None, passed=True)

    async def test_ignores_subresource_requests(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([(["https://filmfans.org/external/abc"], 200)])
        context.new_page = AsyncMock(return_value=page)
        pool = StealthPool(browser_pool=shared_pool)
        callbacks: list[object] = []
        page.on = MagicMock(side_effect=lambda event, cb: callbacks.append(cb))

        async def _goto(url: str, **_: object) -> MagicMock:
            callbacks[0](_request("https://cdn.example/app.js", navigation=False))
            return MagicMock(status=200)

        page.goto = AsyncMock(side_effect=_goto)

        assert await pool.resolve_redirect(
            "https://filmfans.org/external/abc", timeout=10
        ) == Redirect(None, passed=True)

    async def test_retries_rate_limit(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page(
            [
                (["https://filmfans.org/external/abc"], 429),
                (
                    [
                        "https://filmfans.org/external/abc",
                        "https://filecrypt.cc/Container/x.html",
                    ],
                    200,
                ),
            ]
        )
        page.title = AsyncMock(return_value="Too Many Requests")
        context.new_page = AsyncMock(return_value=page)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.asyncio.sleep",
            AsyncMock(),
        ) as mock_sleep:
            redirect = await StealthPool(browser_pool=shared_pool).resolve_redirect(
                "https://filmfans.org/external/abc", timeout=10
            )

        assert redirect == Redirect(
            "https://filecrypt.cc/Container/x.html", passed=True
        )
        mock_sleep.assert_awaited_once_with(2.0)

    async def test_http_error_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([(["https://filmfans.org/external/gone"], 404)])
        page.title = AsyncMock(return_value="Not Found")
        context.new_page = AsyncMock(return_value=page)

        # An error page without a challenge is the site's verdict: dead
        assert await StealthPool(browser_pool=shared_pool).resolve_redirect(
            "https://filmfans.org/external/gone", timeout=10
        ) == Redirect(None, passed=True)

    @staticmethod
    async def _challenged(solved: bool) -> Redirect:
        """A link-out behind a challenge page that *solved* clears or not,
        without a redirect after it (a dead link-out)."""
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([(["https://filmfans.org/external/gone"], 403)])
        page.title = AsyncMock(return_value="Just a moment...")
        context.new_page = AsyncMock(return_value=page)
        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=solved),
        ):
            return await StealthPool(browser_pool=shared_pool).resolve_redirect(
                "https://filmfans.org/external/gone", timeout=10
            )

    async def test_a_dead_link_out_behind_a_cleared_challenge_is_passed(
        self,
    ) -> None:
        """Code review 2026-10-12, finding 14: the browser got past the
        challenge, and the link-out leads nowhere."""
        assert await self._challenged(solved=True) == Redirect(None, passed=True)

    async def test_an_unsolved_challenge_is_not_passed(self) -> None:
        assert await self._challenged(solved=False) == Redirect(None, passed=False)

    async def test_a_navigation_error_is_not_passed(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([])
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_CONNECTION_RESET"))
        context.new_page = AsyncMock(return_value=page)

        assert await StealthPool(browser_pool=shared_pool).resolve_redirect(
            "https://filmfans.org/external/abc", timeout=10
        ) == Redirect(None, passed=False)


# ------------------------------------------------------------------
# click_through
# ------------------------------------------------------------------

_EPISODE = "https://s.to/serie/x/staffel-1/episode-2"
_PASS_WIDGET = "scavengarr.infrastructure.browser.stealth_pool.pass_turnstile_widget"


def _click_page(
    *,
    on_click: list[list[str]],
    on_load: list[list[str]] | None = None,
) -> tuple[MagicMock, object]:
    """Page whose link box click requests each redirect chain in *on_click*.

    A chain is a list of URLs, each hop ``redirected_from`` the one before
    (e.g. the player iframe loading the link-out, then its 302 target).
    Returns the page and a function that emits more chains.
    """
    page = _mock_page(title="Breaking Bad S01E02")
    listeners: list[object] = []

    def _on(event: str, callback: object) -> None:
        if event == "request":
            listeners.append(callback)

    def _emit(chains: list[list[str]]) -> None:
        for chain in chains:
            previous: MagicMock | None = None
            for url in chain:
                previous = _request(url, redirected_from=previous)
                for callback in listeners:
                    callback(previous)  # type: ignore[operator]

    async def _goto(url: str, **_: object) -> MagicMock:
        _emit(on_load or [])
        return MagicMock(status=200)

    async def _click(**_: object) -> None:
        _emit(on_click)

    box = MagicMock()
    box.click = AsyncMock(side_effect=_click)
    page.add_style_tag = AsyncMock(return_value=MagicMock(evaluate=AsyncMock()))
    page.on = MagicMock(side_effect=_on)
    page.goto = AsyncMock(side_effect=_goto)
    page.route = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock(first=box))
    page.wait_for_timeout = AsyncMock()
    page.context = MagicMock()
    page.context.cookies = AsyncMock(
        return_value=[{"name": "laravel_session", "value": "s1"}]
    )
    return page, _emit


class TestStealthPoolClickThrough:
    """s.to opens hosters in a player iframe via /r?t= link-outs, sometimes
    behind a Turnstile gate; its session skips the gate afterwards."""

    async def test_returns_link_out_target_and_site_cookies(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page, _ = _click_page(on_click=[["https://s.to/r?t=1", "https://voe.sx/e/a"]])
        context.new_page = AsyncMock(return_value=page)

        with patch(_PASS_WIDGET, AsyncMock(return_value=False)):
            result = await StealthPool(browser_pool=shared_pool).click_through(
                _EPISODE, "button.link-box", timeout=5
            )

        assert result == ClickThrough(
            url="https://voe.sx/e/a", cookies={"laravel_session": "s1"}
        )
        page.locator.assert_called_with("button.link-box")
        page.context.cookies.assert_awaited_once_with(_EPISODE)
        page.close.assert_awaited_once()

    async def test_only_the_link_box_and_gate_widget_take_clicks(self) -> None:
        """s.to's ad script lays layers over the page, under random class
        names and after a delay ("<div …> subtree intercepts pointer events",
        2026-10-04): over the link box, then over the gate's Turnstile widget
        the click brings up. Until the page closes nothing but these two
        takes pointer events."""
        shared_pool, _, context = _mock_pool_stack()
        page, _ = _click_page(on_click=[["https://s.to/r?t=1", "https://voe.sx/e/a"]])
        context.new_page = AsyncMock(return_value=page)
        steps: list[str] = []
        page.add_style_tag = AsyncMock(side_effect=lambda **_k: steps.append("isolate"))
        box = page.locator.return_value.first
        click = box.click.side_effect

        async def _click(**kwargs: object) -> None:
            steps.append("click")
            await click(**kwargs)

        box.click = AsyncMock(side_effect=_click)

        with patch(_PASS_WIDGET, AsyncMock(return_value=False)):
            result = await StealthPool(browser_pool=shared_pool).click_through(
                _EPISODE, "button.link-box", timeout=5
            )

        assert result is not None
        assert steps == ["isolate", "click"]
        css = page.add_style_tag.await_args.kwargs["content"]
        assert "* { pointer-events: none !important; }" in css
        targets = "button.link-box, form:has([name='cf-turnstile-response'])"
        assert f":is({targets}), :is({targets}) *" in css

    async def test_ignores_offsite_pages_not_redirected_by_the_site(self) -> None:
        # Ad frames load other hosts directly or through their own redirects
        shared_pool, _, context = _mock_pool_stack()
        page, _ = _click_page(
            on_load=[
                ["https://ads.example/frame"],
                ["https://ads.example/go", "https://tracker.example/x"],
            ],
            on_click=[["https://s.to/r?t=1", "https://voe.sx/e/a"]],
        )
        context.new_page = AsyncMock(return_value=page)

        with patch(_PASS_WIDGET, AsyncMock(return_value=False)):
            result = await StealthPool(browser_pool=shared_pool).click_through(
                _EPISODE, "button.link-box", timeout=5
            )

        assert result is not None
        assert result.url == "https://voe.sx/e/a"

    async def test_passes_the_turnstile_gate(self) -> None:
        # The link-out shows the gate; its form POST redirects to the hoster
        shared_pool, _, context = _mock_pool_stack()
        page, emit = _click_page(on_click=[["https://s.to/r?t=1"]])
        context.new_page = AsyncMock(return_value=page)

        async def _pass(_page: object, *, timeout_ms: int) -> bool:
            emit([["https://s.to/r", "https://voe.sx/e/a"]])  # type: ignore[operator]
            return True

        with patch(_PASS_WIDGET, AsyncMock(side_effect=_pass)) as passed:
            result = await StealthPool(browser_pool=shared_pool).click_through(
                _EPISODE, "button.link-box", timeout=5
            )

        assert result is not None
        assert result.url == "https://voe.sx/e/a"
        passed.assert_awaited_once()

    async def test_submitted_gate_gets_time_for_its_redirect(self) -> None:
        """On a Raspberry Pi the gate's widget used 28 of the 30 s budget, and
        the redirect after its form came later: the passed gate was thrown
        away (stealth_click_through_no_target, 2026-10-04)."""
        shared_pool, _, context = _mock_pool_stack()
        page, emit = _click_page(on_click=[["https://s.to/r?t=1"]])
        context.new_page = AsyncMock(return_value=page)
        clock = {"now": 0.0}

        async def _pass(_page: object, *, timeout_ms: int) -> bool:
            clock["now"] += timeout_ms / 1000 - 0.1
            return True

        async def _poll(ms: int) -> None:
            clock["now"] += ms / 1000
            if clock["now"] > 7.0:  # 2 s past the budget
                emit([["https://s.to/r", "https://voe.sx/e/a"]])  # type: ignore[operator]

        page.wait_for_timeout = AsyncMock(side_effect=_poll)

        with (
            patch(_PASS_WIDGET, AsyncMock(side_effect=_pass)),
            patch(
                "scavengarr.infrastructure.browser.stealth_pool.time",
                MagicMock(monotonic=lambda: clock["now"]),
            ),
        ):
            result = await StealthPool(browser_pool=shared_pool).click_through(
                _EPISODE, "button.link-box", timeout=5
            )

        assert result is not None
        assert result.url == "https://voe.sx/e/a"

    async def test_nothing_leaves_the_site_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page, _ = _click_page(on_click=[["https://s.to/r?t=1"]])
        context.new_page = AsyncMock(return_value=page)

        with patch(_PASS_WIDGET, AsyncMock(return_value=False)):
            result = await StealthPool(browser_pool=shared_pool).click_through(
                _EPISODE, "button.link-box", timeout=0.05
            )

        assert result is None
        page.close.assert_awaited_once()

    async def test_navigation_error_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page, _ = _click_page(on_click=[])
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_TIMED_OUT"))
        context.new_page = AsyncMock(return_value=page)

        result = await StealthPool(browser_pool=shared_pool).click_through(
            _EPISODE, "button.link-box", timeout=5
        )

        assert result is None
        page.close.assert_awaited_once()


# ------------------------------------------------------------------
# capture_media
# ------------------------------------------------------------------


def _player_page(
    *,
    on_load: list[str] | None = None,
    on_click: list[list[str]] | None = None,
    status: int = 200,
    media_requests: frozenset[str] = frozenset(),
    documents: frozenset[str] = frozenset(),
) -> MagicMock:
    """Page whose player requests *on_load* URLs while loading and the
    i-th list of *on_click* on the i-th mouse click; URLs in
    *media_requests* come from the video element (resource type "media"),
    those in *documents* load a frame. Playwright calls the request
    listeners outside the capture's context, so do these."""
    page = _mock_page(title="Player")
    listeners: list[Callable[[MagicMock], None]] = []

    def _on(event: str, callback: Callable[[MagicMock], None]) -> None:
        if event == "request":
            listeners.append(callback)

    def _emit(urls: list[str]) -> None:
        for url in urls:
            request = _request(url, navigation=False)
            request.headers = {"referer": "https://player.example/"}
            request.resource_type = (
                "media"
                if url in media_requests
                else "document"
                if url in documents
                else "script"
            )
            for callback in listeners:
                contextvars.Context().run(callback, request)

    async def _goto(url: str, **_: object) -> MagicMock:
        _emit(on_load or [])
        return MagicMock(status=status)

    clicks = iter(on_click or [])

    async def _click(*_: object) -> None:
        _emit(next(clicks, []))

    page.on = MagicMock(side_effect=_on)
    page.goto = AsyncMock(side_effect=_goto)
    page.route = AsyncMock()
    page.viewport_size = {"width": 1280, "height": 720}
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock(side_effect=_click)
    return page


@pytest.fixture
def _fast_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    from scavengarr.infrastructure.browser import stealth_pool as mod

    monkeypatch.setattr(mod, "_MEDIA_AUTOPLAY_WAIT_S", 0.01)
    monkeypatch.setattr(mod, "_MEDIA_CLICK_WAIT_S", 0.01)


@pytest.mark.usefixtures("_fast_capture")
class TestStealthPoolCaptureMedia:
    async def _capture(self, page: MagicMock) -> object:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(return_value=page)
        return await StealthPool(browser_pool=shared_pool).capture_media(
            "https://filemoon.to/e/abc", timeout=10
        )

    async def test_without_a_timeout_the_pools_own_applies(self) -> None:
        """``stremio.probe_stealth_timeout_seconds`` reaches the pool as
        ``timeout_ms`` and bounds a capture the caller gives no time (step 51)."""
        shared_pool, _, context = _mock_pool_stack()
        page = _player_page(on_load=["https://cdn.example/x.m3u8"])
        context.new_page = AsyncMock(return_value=page)
        pool = StealthPool(browser_pool=shared_pool, timeout_ms=7_000)

        media = await pool.capture_media("https://filemoon.to/e/abc")

        assert media is not None
        assert page.goto.await_args.kwargs["timeout"] == 7_000

    async def test_autoplay_request_is_captured(self) -> None:
        page = _player_page(
            on_load=[
                "https://player.example/assets/app.js",
                "https://cdn.example/hls2/abc/master.m3u8?t=tok",
            ]
        )

        media = await self._capture(page)

        assert media is not None
        assert media.url == "https://cdn.example/hls2/abc/master.m3u8?t=tok"
        assert media.referer == "https://player.example/"
        page.mouse.click.assert_not_awaited()
        page.close.assert_awaited_once()

    async def test_clicks_player_until_media_is_requested(self) -> None:
        """First click only opens an ad popup, the second starts playback."""
        page = _player_page(on_click=[[], ["https://cdn.example/v/x.mp4?t=1"]])

        media = await self._capture(page)

        assert media is not None
        assert media.url == "https://cdn.example/v/x.mp4?t=1"
        assert page.mouse.click.await_count == 2
        page.mouse.click.assert_awaited_with(640, 360)

    @staticmethod
    def _locate(page: MagicMock, *, count: int, box: dict[str, float] | None) -> None:
        """*page*'s locator finds *count* elements, the first laid out at *box*."""
        element = MagicMock()
        element.count = AsyncMock(return_value=count)
        element.bounding_box = AsyncMock(return_value=box)
        page.locator = MagicMock(return_value=MagicMock(first=element))

    async def _capture_player(self, page: MagicMock, player: str) -> object:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(return_value=page)
        return await StealthPool(browser_pool=shared_pool).capture_media(
            "https://byse.example/d/abc", timeout=10, player=player
        )

    async def test_a_player_frame_is_clicked_at_its_centre(self) -> None:
        """A watch page shows its player in a frame below its header (Byse
        ``/d/``): the click goes to the frame's centre, the page's centre
        hits the empty top of the player."""
        page = _player_page(on_click=[["https://cdn.example/hls/master.m3u8"]])
        self._locate(
            page, count=1, box={"x": 252, "y": 268, "width": 766, "height": 430}
        )

        media = await self._capture_player(page, ".video-page__player iframe")

        assert media is not None
        page.locator.assert_called_with(".video-page__player iframe")
        page.mouse.click.assert_awaited_once_with(635, 483)

    @pytest.mark.parametrize(
        ("count", "box"), [(0, None), (1, None)], ids=["missing", "not laid out"]
    )
    async def test_without_the_player_element_the_page_centre_is_clicked(
        self, count: int, box: dict[str, float] | None
    ) -> None:
        page = _player_page(on_click=[["https://cdn.example/hls/master.m3u8"]])
        self._locate(page, count=count, box=box)

        media = await self._capture_player(page, ".video-page__player iframe")

        assert media is not None
        page.mouse.click.assert_awaited_once_with(640, 360)

    async def test_video_element_request_without_extension_is_captured(
        self,
    ) -> None:
        """DoodStream's CDN URLs carry no file extension (``…~abc?token=``)."""
        stream = "https://fd304l.cloudatacdn.com/u5kj/msvv~lsPG?token=y5i2&expiry=1"
        page = _player_page(
            on_click=[["https://cdn.example/app.js", stream]],
            media_requests=frozenset({stream}),
        )

        media = await self._capture(page)

        assert media is not None
        assert media.url == stream

    async def test_no_media_after_all_clicks_returns_none(self) -> None:
        from scavengarr.infrastructure.browser import stealth_pool as mod

        page = _player_page(on_click=[["https://ads.example/pop.js"]])

        assert await self._capture(page) is None
        assert page.mouse.click.await_count == mod._MEDIA_PLAY_CLICKS
        page.close.assert_awaited_once()

    async def test_unsolved_challenge_returns_none(self) -> None:
        page = _player_page(status=403)

        with patch(
            "scavengarr.infrastructure.browser.stealth_pool.solve_cloudflare",
            AsyncMock(return_value=False),
        ):
            assert await self._capture(page) is None
        page.mouse.click.assert_not_awaited()

    async def test_navigation_error_returns_none(self) -> None:
        page = _player_page()
        page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_CONNECTION_CLOSED"))

        assert await self._capture(page) is None
        page.close.assert_awaited_once()

    async def test_player_page_loads_styles_but_not_media(self) -> None:
        """Players need CSS for layout (the click lands on the play button);
        the context-wide resource block is overridden for this page only."""
        from scavengarr.infrastructure.browser.stealth_pool import (
            _allow_player_resources,
        )

        page = _player_page(on_load=["https://cdn.example/x.m3u8"])
        await self._capture(page)
        page.route.assert_awaited_once_with("**/*", _allow_player_resources)

        for rtype, aborted in (("stylesheet", False), ("media", True)):
            route = AsyncMock()
            route.request.resource_type = rtype
            await _allow_player_resources(route)
            assert route.abort.await_count == int(aborted)
            assert route.continue_.await_count == int(not aborted)

    async def test_a_capture_of_own_scripts_only_routes_them(self) -> None:
        from scavengarr.infrastructure.browser.stealth_pool import (
            _allow_own_player_resources,
        )

        shared_pool, _, context = _mock_pool_stack()
        page = _player_page(on_load=["https://cdn.example/x.m3u8"])
        context.new_page = AsyncMock(return_value=page)

        await StealthPool(browser_pool=shared_pool).capture_media(
            "https://filemoon.to/e/abc", timeout=10, own_scripts_only=True
        )

        page.route.assert_awaited_once_with("**/*", _allow_own_player_resources)


# The Byse watch page and its player frame (row 78 on the Pi)
_FRAMES = ("https://bysezejataos.com/d/abc", "https://n1mwq.org/xdk4/abc")


_TURNSTILE_FRAME = (
    "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g"
    "/turnstile/if/ov2/av0/rcv/x1/0x4AAAAAAA/auto/fbE/new/normal/auto/"
)
_HOSTER_CAPTCHA = re.compile(r"/captcha(?:/verify)?/?(?:[?#]|$)")


@pytest.mark.usefixtures("_fast_capture")
class TestStealthPoolCaptureChallengeMark:
    """A capture marks the resolution it runs in when its page shows a
    challenge (react-to-challenges, R7): a captcha provider's frame, a
    request the resolver names as its hoster's captcha, an interstitial."""

    @staticmethod
    async def _capture(
        page: MagicMock,
        *,
        challenge_requests: re.Pattern[str] | None = None,
        passive_widget: bool = False,
    ) -> tuple[object, ChallengeMark]:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(return_value=page)
        mark = ChallengeMark()
        challenge_mark.set(mark)
        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://byse.example/e/abc",
            timeout=10,
            challenge_requests=challenge_requests,
            passive_widget=passive_widget,
        )
        return media, mark

    async def test_a_captcha_providers_frame_marks(self) -> None:
        page = _player_page(
            on_load=[_TURNSTILE_FRAME],
            on_click=[["https://cdn.example/hls/master.m3u8"]],
            documents=frozenset({_TURNSTILE_FRAME}),
        )

        media, mark = await self._capture(page)

        assert media is not None
        assert (mark.kind, mark.open) == ("turnstile", True)

    async def test_a_passive_widgets_frame_marks_nothing(self) -> None:
        """DoodStream's player loads an invisible Turnstile on every capture,
        cold or warm, and streams before its token (code review 2026-10-12,
        finding 3): its frame is part of the player, no challenge."""
        page = _player_page(
            on_load=[_TURNSTILE_FRAME],
            on_click=[["https://cdn.example/hls/master.m3u8"]],
            documents=frozenset({_TURNSTILE_FRAME}),
        )

        media, mark = await self._capture(page, passive_widget=True)

        assert media is not None
        assert mark.kind is None

    async def test_a_passive_widget_leaves_an_interstitial_marked(self) -> None:
        page = _player_page(on_click=[["https://cdn.example/hls/master.m3u8"]])
        titles = iter(["Just a moment..."])
        page.title = AsyncMock(side_effect=lambda: next(titles, "Player"))

        _, mark = await self._capture(page, passive_widget=True)

        assert mark.kind == "cloudflare_page"

    async def test_a_dead_file_behind_a_passed_interstitial_is_passed(self) -> None:
        """The review's Dropload case (finding 2): the capture gets past the
        interstitial and the page says the file is gone. A verdict on the
        link, not a challenge."""
        page = _player_page(on_click=[["https://cdn.example/hls/master.m3u8"]])
        titles = iter(["Just a moment..."])
        page.title = AsyncMock(side_effect=lambda: next(titles, "Player"))
        page.evaluate = AsyncMock(return_value="Video not found")

        media, mark = await self._capture(page)

        assert media is None
        assert (mark.kind, mark.open) == ("cloudflare_page", False)

    @pytest.mark.parametrize("after_click", [False, True])
    async def test_an_offline_notice_passes_a_standing_challenge(
        self, after_click: bool
    ) -> None:
        """The page's own verdict: whatever challenge came before, the file
        is gone (some players tell only after the click)."""
        page = _player_page(
            on_load=[_TURNSTILE_FRAME], documents=frozenset({_TURNSTILE_FRAME})
        )
        gone = "File is no longer available as it expired or has been deleted."
        page.evaluate = AsyncMock(side_effect=["Play", gone] if after_click else [gone])

        media, mark = await self._capture(page)

        assert media is None
        assert (mark.kind, mark.open) == ("turnstile", False)

    async def test_a_providers_script_alone_marks_nothing(self) -> None:
        """Loading the widget's script shows no challenge yet."""
        page = _player_page(
            on_load=["https://challenges.cloudflare.com/turnstile/v0/api.js"],
            on_click=[["https://cdn.example/hls/master.m3u8"]],
        )

        _, mark = await self._capture(page)

        assert mark.kind is None

    @pytest.mark.parametrize(
        "captcha",
        [
            "https://byse.example/api/videos/abc/captcha",
            "https://byse.example/api/videos/abc/captcha/verify?t=1",
        ],
    )
    async def test_a_request_the_resolver_names_marks(self, captcha: str) -> None:
        page = _player_page(on_click=[[captcha], []])

        media, mark = await self._capture(page, challenge_requests=_HOSTER_CAPTCHA)

        assert media is None
        assert mark.kind == "hoster_captcha"

    async def test_requests_the_resolver_does_not_name_mark_nothing(self) -> None:
        page = _player_page(
            on_load=["https://byse.example/e/captchas"],
            on_click=[["https://cdn.example/hls/master.m3u8"]],
        )

        _, mark = await self._capture(page, challenge_requests=_HOSTER_CAPTCHA)

        assert mark.kind is None

    async def test_an_interstitial_marks(self) -> None:
        page = _player_page(on_click=[["https://cdn.example/hls/master.m3u8"]])
        titles = iter(["Just a moment..."])
        page.title = AsyncMock(side_effect=lambda: next(titles, "Player"))

        media, mark = await self._capture(page)

        assert media is not None
        assert (mark.kind, mark.open) == ("cloudflare_page", False)

    async def test_a_plain_player_marks_nothing(self) -> None:
        page = _player_page(
            on_load=["https://player.example/assets/app.js"],
            on_click=[["https://cdn.example/hls/master.m3u8"]],
        )

        media, mark = await self._capture(page, challenge_requests=_HOSTER_CAPTCHA)

        assert media is not None
        assert mark.kind is None

    async def test_outside_a_resolution_a_frame_marks_nothing(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(
            return_value=_player_page(
                on_load=[_TURNSTILE_FRAME, "https://cdn.example/hls/master.m3u8"],
                documents=frozenset({_TURNSTILE_FRAME}),
            )
        )

        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://byse.example/e/abc", timeout=10
        )

        assert media is not None
        assert challenge_mark.get() is None


class _Request:
    """A route's request: its frame's page lists *frames*; ``None``: the
    request has no frame (a service worker's)."""

    def __init__(
        self, url: str, resource_type: str, frames: tuple[str, ...] | None = _FRAMES
    ) -> None:
        self.url = url
        self.resource_type = resource_type
        self._frames = frames

    @property
    def frame(self) -> SimpleNamespace:
        if self._frames is None:
            raise PlaywrightError("no frame")
        frames = [SimpleNamespace(url=url) for url in self._frames]
        return SimpleNamespace(page=SimpleNamespace(frames=frames))


class TestOwnScriptsOnly:
    """A capture that opts in runs the scripts of the page's and its
    frames' sites only: an ad tag in the player frame turns every click
    into a popup (Filemoon on the Pi)."""

    def test_a_script_of_another_site_is_foreign(self) -> None:
        from scavengarr.infrastructure.browser.stealth_pool import _foreign_script

        assert _foreign_script("script", "https://llvpn.com/tag.min.js", _FRAMES)
        assert _foreign_script(
            "script", "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js", _FRAMES
        )
        # The page's and the player frame's own scripts, subdomains included
        assert not _foreign_script(
            "script", "https://n1mwq.org/player/x/jwplayer.js", _FRAMES
        )
        assert not _foreign_script(
            "script", "https://cdn.bysezejataos.com/assets/index.js", _FRAMES
        )
        # Requests other than scripts are not its business
        assert not _foreign_script("xhr", "https://rtmark.net/gid.js", _FRAMES)
        assert not _foreign_script("media", "https://cdn.example/x.mp4", _FRAMES)

    def test_the_challenge_widget_is_no_foreign_script(self) -> None:
        from scavengarr.infrastructure.browser.stealth_pool import _foreign_script

        # A Turnstile widget the page itself loads must run, or the page
        # never passes its challenge
        assert not _foreign_script(
            "script",
            "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
            _FRAMES,
        )
        # By exact host: cdnjs.cloudflare.com serves any library, and a
        # look-alike host is no Cloudflare
        assert _foreign_script(
            "script",
            "https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js",
            _FRAMES,
        )
        assert _foreign_script(
            "script", "https://challenges.cloudflare.com.llvpn.com/tag.js", _FRAMES
        )
        # The ad tag stays foreign
        assert _foreign_script("script", "https://llvpn.com/tag.min.js", _FRAMES)

    async def test_a_foreign_script_is_aborted_as_the_clients_block(self) -> None:
        from scavengarr.infrastructure.browser.stealth_pool import (
            _allow_own_player_resources,
        )

        route = AsyncMock()
        route.request = _Request("https://llvpn.com/tag.min.js", "script")

        with structlog.testing.capture_logs() as logs:
            await _allow_own_player_resources(route)

        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_awaited()
        (event,) = [e for e in logs if e["event"] == "stealth_capture_script_blocked"]
        assert event["domain"] == "llvpn.com"

    @pytest.mark.parametrize(
        ("request_", "aborted"),
        [
            (_Request("https://n1mwq.org/player/x/jwplayer.js", "script"), False),
            # The top page's Turnstile widget
            (
                _Request(
                    "https://challenges.cloudflare.com/turnstile/v0/api.js", "script"
                ),
                False,
            ),
            (_Request("https://llvpn.com/banner.css", "stylesheet"), False),
            # The media cut stays
            (_Request("https://cdn.example/x.mp4", "media"), True),
            # Without its frame the request passes as before
            (_Request("https://llvpn.com/tag.min.js", "script", frames=None), False),
        ],
    )
    async def test_everything_else_keeps_the_players_route(
        self, request_: _Request, aborted: bool
    ) -> None:
        from scavengarr.infrastructure.browser.stealth_pool import (
            _allow_own_player_resources,
        )

        route = AsyncMock()
        route.request = request_

        await _allow_own_player_resources(route)

        if aborted:
            route.abort.assert_awaited_once_with()
        else:
            route.abort.assert_not_awaited()
        assert route.continue_.await_count == int(not aborted)


@pytest.mark.usefixtures("_fast_capture")
class TestStealthPoolCaptureMediaOffline:
    """Dead files: the player page says so, no need to click for 18 s."""

    @pytest.mark.parametrize(
        "text",
        [
            "Video not found",  # DoodStream / playmogo
            "File is no longer available as it expired or has been deleted.",
            "No such file",  # GoodStream
            "The video has been removed.",  # Veev
        ],
    )
    async def test_offline_player_page_returns_none_without_clicking(
        self, text: str
    ) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _player_page(on_click=[["https://cdn.example/x.m3u8"]])
        page.evaluate = AsyncMock(return_value=text)
        context.new_page = AsyncMock(return_value=page)

        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://playmogo.com/e/abc", timeout=10
        )

        assert media is None
        page.mouse.click.assert_not_awaited()

    async def test_player_text_mentioning_errors_is_not_offline(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _player_page(on_click=[["https://cdn.example/x.m3u8"]])
        page.evaluate = AsyncMock(return_value="Oppenheimer 2023 1080p  Play")
        context.new_page = AsyncMock(return_value=page)

        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://filemoon.to/e/abc", timeout=10
        )

        assert media is not None

    async def test_offline_notice_after_click_stops_clicking(self) -> None:
        """savefiles: the play click posts the XFS form, /dl says the file
        is gone; no further clicks."""
        shared_pool, _, context = _mock_pool_stack()
        page = _player_page()
        page.evaluate = AsyncMock(
            side_effect=[
                "Play",
                "File is no longer available as it expired or has been deleted.",
            ]
        )
        context.new_page = AsyncMock(return_value=page)

        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://savefiles.com/e/abc", timeout=10
        )

        assert media is None
        assert page.mouse.click.await_count == 1

    async def test_unreadable_page_text_is_not_offline(self) -> None:
        """A navigation in flight destroys the JS context; keep clicking."""
        shared_pool, _, context = _mock_pool_stack()
        page = _player_page(on_click=[[], ["https://cdn.example/x.m3u8"]])
        page.evaluate = AsyncMock(side_effect=RuntimeError("context destroyed"))
        context.new_page = AsyncMock(return_value=page)

        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://savefiles.com/e/abc", timeout=10
        )

        assert media is not None


@pytest.mark.usefixtures("_fast_capture")
class TestStealthPoolCaptureMediaThumbnails:
    async def test_thumbnail_playlist_is_not_the_stream(self) -> None:
        """vixeo loads a seek-preview playlist (thumbnails.m3u8) before the
        video; taking it would hand players a playlist of JPEGs."""
        shared_pool, _, context = _mock_pool_stack()
        page = _player_page(
            on_load=[
                "https://vixeo.io/thumbnails/KD2Ztr3euxYK/thumbnails.m3u8",
                "https://sfy-01-fr.vidsonic.net/secure/98/x/video.mp4/index.m3u8?e=1",
            ]
        )
        context.new_page = AsyncMock(return_value=page)

        media = await StealthPool(browser_pool=shared_pool).capture_media(
            "https://vixeo.io/e/KD2Ztr3euxYK", timeout=10
        )

        assert media is not None
        assert media.url.startswith("https://sfy-01-fr.vidsonic.net/")


class TestBusyPages:
    """Work due before a page could do it gets none."""

    @staticmethod
    def _too_late() -> None:
        page_claim.set(PageClaim("plugin", time.monotonic() + 1.0))

    async def test_a_plugin_page_answers_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)
        self._too_late()

        assert await pool.fetch_text("https://x.org/a", timeout=30) is None
        assert await pool.resolve_redirect("https://x.org/b", timeout=30) == Redirect(
            None, passed=False
        )
        assert await pool.click_through("https://x.org/c", "a", timeout=30) is None
        context.new_page.assert_not_awaited()

    async def test_a_capture_reports_it(self) -> None:
        """The registry neither caches nor counts it."""
        shared_pool, _, context = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)
        self._too_late()

        with pytest.raises(PageBusy):
            await pool.capture_media("https://filemoon.sx/e/a", timeout=15)
        context.new_page.assert_not_awaited()


# ------------------------------------------------------------------
# The capture state (keep-capture-state, row 91's R4)
# ------------------------------------------------------------------


class TestCreationState:
    async def test_the_context_is_created_with_the_stores_state(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()
        store = AsyncMock()
        state = {"cookies": [], "origins": []}
        store.creation_state = AsyncMock(return_value=state)
        pool = StealthPool(browser_pool=shared_pool, clearance_store=store)

        assert await pool._ensure_context() is context

        browser.new_context.assert_awaited_once_with(
            service_workers="block", storage_state=state
        )
        store.restore.assert_not_awaited()

    async def test_without_a_state_the_context_is_plain(self) -> None:
        shared_pool, browser, _ = _mock_pool_stack()
        store = AsyncMock()
        store.creation_state = AsyncMock(return_value=None)

        await StealthPool(
            browser_pool=shared_pool, clearance_store=store
        )._ensure_context()

        browser.new_context.assert_awaited_once_with(service_workers="block")

    async def test_a_failed_creation_with_the_state_falls_back(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()
        browser.new_context = AsyncMock(
            side_effect=[PlaywrightError("bad storage state"), context]
        )
        store = AsyncMock()
        store.creation_state = AsyncMock(return_value={"cookies": [], "origins": []})
        pool = StealthPool(browser_pool=shared_pool, clearance_store=store)

        with structlog.testing.capture_logs() as logs:
            assert await pool._ensure_context() is context

        assert browser.new_context.await_args_list[-1].kwargs == {
            "service_workers": "block"
        }
        assert any(e["event"] == "capture_state_restore_failed" for e in logs)

    async def test_a_recycles_new_context_gets_the_state_too(self) -> None:
        shared_pool, browser, _ = _mock_pool_stack()
        store = AsyncMock()
        state = {"cookies": [], "origins": []}
        store.creation_state = AsyncMock(return_value=state)
        pool = StealthPool(browser_pool=shared_pool, clearance_store=store)
        await pool._ensure_context()

        browser.is_connected = MagicMock(return_value=False)
        new_browser = AsyncMock()
        new_browser.new_context = AsyncMock(return_value=AsyncMock())
        new_browser.is_connected = MagicMock(return_value=True)
        shared_pool.warmup = AsyncMock(return_value=(new_browser, MagicMock()))
        await pool._ensure_context()

        new_browser.new_context.assert_awaited_once_with(
            service_workers="block", storage_state=state
        )


def _frame(url: str, state: dict[str, object] | None = None) -> MagicMock:
    """A frame at *url* whose origin's storage the collector reads as
    *state* (None: an opaque origin)."""
    frame = MagicMock()
    frame.url = url
    frame.evaluate = AsyncMock(return_value=state)
    return frame


def _origin(origin: str, *keys: str) -> dict[str, object]:
    return {
        "origin": origin,
        "localStorage": [{"name": key, "value": "v"} for key in keys],
        "indexedDB": [],
    }


_KEPT_COOKIE = {
    "name": "file_id",
    "value": "v",
    "domain": "dr0pstream.com",
    "path": "/",
}
_PLAYER = _origin("https://dr0pstream.com", "player")
_WIDGET = _origin("https://challenges.cloudflare.com", "widget")


async def _expired_clock() -> asyncio.Timeout:
    """A resolve bound that cut its work (a completion runs on past it)."""
    clock = asyncio.timeout(0)
    with pytest.raises(TimeoutError):
        async with clock:
            await asyncio.sleep(1)
    return clock


@pytest.mark.usefixtures("_fast_capture")
class TestCaptureKeepsItsState:
    """After a stream the capture reads its hosts' state from its own page
    before the page closes, so no other origin is visited (code review
    2026-10-12, finding 6); the store writes it in the background."""

    @staticmethod
    def _setup(
        *, streams: bool = True, frames: Callable[[], list[MagicMock]] | None = None
    ) -> tuple[StealthPool, AsyncMock, AsyncMock, list[MagicMock]]:
        shared_pool, _, context = _mock_pool_stack()
        store = AsyncMock()
        store.creation_state = AsyncMock(return_value=None)
        on_load = ["https://cdn.example/hls/master.m3u8"] if streams else []
        pages: list[MagicMock] = []

        def _frames() -> list[MagicMock]:
            return [
                _frame("https://dr0pstream.com/e/abc", _PLAYER),
                _frame("https://challenges.cloudflare.com/cdn-cgi/x", _WIDGET),
                _frame("about:blank"),
            ]

        def _page() -> MagicMock:
            page = _player_page(on_load=on_load)
            page.frames = (frames or _frames)()
            page.context = context
            pages.append(page)
            return page

        context.new_page = AsyncMock(side_effect=lambda: _page())
        context.cookies = AsyncMock(return_value=[_KEPT_COOKIE])
        pool = StealthPool(browser_pool=shared_pool, clearance_store=store)
        return pool, context, store, pages

    @staticmethod
    async def _settle(pool: StealthPool) -> None:
        await asyncio.gather(*pool._keep_tasks)

    async def test_a_stream_keeps_the_pages_and_frames_hosts(self) -> None:
        pool, context, store, pages = self._setup()

        media = await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        assert media is not None
        store.keep_capture_state.assert_awaited_once_with(
            ["challenges.cloudflare.com", "dr0pstream.com"],
            {"cookies": [_KEPT_COOKIE], "origins": [_PLAYER, _WIDGET]},
            duration_ms=ANY,
        )
        assert isinstance(
            store.keep_capture_state.await_args.kwargs["duration_ms"], int
        )
        pages[0].frames[2].evaluate.assert_not_awaited()  # about:blank

    async def test_the_state_is_read_before_the_page_closes(self) -> None:
        pool, context, _, pages = self._setup()
        read_when_closed: list[list[int]] = []
        new_page = context.new_page.side_effect

        def _page() -> MagicMock:
            page = new_page()
            page.close = AsyncMock(
                side_effect=lambda: read_when_closed.append(
                    [frame.evaluate.await_count for frame in page.frames]
                )
            )
            return page

        context.new_page = AsyncMock(side_effect=_page)

        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)

        assert read_when_closed == [[1, 1, 0]]
        context.storage_state.assert_not_awaited()  # no page of its own
        assert context.new_page.await_count == 1

    async def test_an_origin_is_read_once(self) -> None:
        pool, _, store, pages = self._setup(
            frames=lambda: [
                _frame("https://dr0pstream.com/e/abc", _PLAYER),
                _frame("https://dr0pstream.com/player", _PLAYER),
            ]
        )

        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        assert [f.evaluate.await_count for f in pages[0].frames] == [1, 0]
        assert store.keep_capture_state.await_args.args[1]["origins"] == [_PLAYER]

    async def test_an_opaque_origin_keeps_its_hosts_cookies_only(self) -> None:
        pool, _, store, _ = self._setup(
            frames=lambda: [_frame("https://dr0pstream.com/e/abc", None)]
        )

        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        store.keep_capture_state.assert_awaited_once_with(
            ["dr0pstream.com"],
            {"cookies": [_KEPT_COOKIE], "origins": []},
            duration_ms=ANY,
        )

    async def test_an_origin_without_storage_is_left_out(self) -> None:
        """As in ``storage_state()``: a new context visits no origin for
        nothing."""
        pool, _, store, _ = self._setup(
            frames=lambda: [
                _frame(
                    "https://dr0pstream.com/e/abc", _origin("https://dr0pstream.com")
                ),
                _frame("https://challenges.cloudflare.com/cdn-cgi/x", _WIDGET),
            ]
        )

        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        assert store.keep_capture_state.await_args.args[1]["origins"] == [_WIDGET]

    async def test_the_read_waits_at_most_its_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scavengarr.infrastructure.browser import stealth_pool as mod

        assert mod._KEEP_BOUND_S == 0.5
        monkeypatch.setattr(mod, "_KEEP_BOUND_S", 0.01)

        async def _never(*_: object) -> None:
            await asyncio.Event().wait()

        hanging = _frame("https://dr0pstream.com/e/abc")
        hanging.evaluate = AsyncMock(side_effect=_never)
        pool, _, store, _ = self._setup(frames=lambda: [hanging])

        with structlog.testing.capture_logs() as logs:
            media = await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        assert media is not None
        (failed,) = [e for e in logs if e["event"] == "capture_state_save_failed"]
        assert failed["error"] == "TimeoutError"
        store.keep_capture_state.assert_not_awaited()
        assert not pool._kept  # the next stream tries again

    async def test_nothing_is_read_when_the_bound_ends_sooner(self) -> None:
        """A stream found within ``_KEEP_BOUND_S`` of the resolve bound goes
        out as found: the read could cost it."""
        pool, _, store, pages = self._setup()

        async with asyncio.timeout(0.3) as clock:
            token = work_clock.set(clock)
            try:
                media = await pool.capture_media(
                    "https://dr0pstream.com/e/abc", timeout=10
                )
            finally:
                work_clock.reset(token)
        await self._settle(pool)

        assert media is not None
        assert all(f.evaluate.await_count == 0 for f in pages[0].frames)
        store.keep_capture_state.assert_not_awaited()
        assert not pool._kept

    async def test_a_bound_with_time_left_reads(self) -> None:
        pool, _, store, _ = self._setup()

        async with asyncio.timeout(10) as clock:
            token = work_clock.set(clock)
            try:
                await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
            finally:
                work_clock.reset(token)
        await self._settle(pool)

        store.keep_capture_state.assert_awaited_once()

    async def test_a_completion_past_its_bound_reads(self) -> None:
        """A capture cut after its challenge runs on past its bound, which
        no longer cuts it: its stream keeps the state it passed with."""
        pool, _, store, _ = self._setup()
        token = work_clock.set(await _expired_clock())
        try:
            await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        finally:
            work_clock.reset(token)
        await self._settle(pool)

        store.keep_capture_state.assert_awaited_once()

    async def test_the_store_writes_in_the_background(self) -> None:
        pool, _, store, _ = self._setup()
        release = asyncio.Event()

        async def _slow_write(*_: object, **__: object) -> None:
            await release.wait()

        store.keep_capture_state = AsyncMock(side_effect=_slow_write)

        media = await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)

        assert media is not None
        assert pool._keep_tasks
        release.set()
        await self._settle(pool)
        store.keep_capture_state.assert_awaited_once()

    async def test_a_second_stream_within_10_minutes_keeps_nothing(self) -> None:
        pool, _, store, pages = self._setup()

        for _ in range(2):
            await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
            await self._settle(pool)

        assert pages[1].frames[0].evaluate.await_count == 0
        assert store.keep_capture_state.await_count == 1

    async def test_after_the_interval_the_host_is_kept_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scavengarr.infrastructure.browser import stealth_pool as mod

        assert mod._KEEP_INTERVAL_S == 600
        pool, _, store, _ = self._setup()
        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)
        monkeypatch.setattr(mod, "_KEEP_INTERVAL_S", 0.0)

        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        assert store.keep_capture_state.await_count == 2

    async def test_without_a_stream_nothing_is_kept(self) -> None:
        pool, context, store, pages = self._setup(streams=False)

        media = await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)

        assert media is None
        assert not pool._keep_tasks
        assert all(f.evaluate.await_count == 0 for f in pages[0].frames)
        context.cookies.assert_not_awaited()
        store.keep_capture_state.assert_not_awaited()

    async def test_a_failed_read_is_logged_and_tried_again_next_time(self) -> None:
        failing = [True]

        def _frames() -> list[MagicMock]:
            frame = _frame("https://dr0pstream.com/e/abc", _PLAYER)
            if failing.pop() if failing else False:
                frame.evaluate = AsyncMock(
                    side_effect=PlaywrightError("Execution context was destroyed")
                )
            return [frame]

        pool, _, store, _ = self._setup(frames=_frames)

        with structlog.testing.capture_logs() as logs:
            await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
            await self._settle(pool)
        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        (failed,) = [e for e in logs if e["event"] == "capture_state_save_failed"]
        assert failed["hosts"] == ["dr0pstream.com"]
        assert "https://" not in str(failed)
        store.keep_capture_state.assert_awaited_once()

    async def test_a_failed_write_is_logged_and_tried_again_next_time(self) -> None:
        pool, _, store, _ = self._setup()
        store.keep_capture_state = AsyncMock(side_effect=[OSError("disk full"), None])

        with structlog.testing.capture_logs() as logs:
            await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
            await self._settle(pool)
        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await self._settle(pool)

        (failed,) = [e for e in logs if e["event"] == "capture_state_save_failed"]
        assert failed["error"] == "OSError"
        assert store.keep_capture_state.await_count == 2

    async def test_the_tasks_end_at_the_pools_cleanup(self) -> None:
        pool, _, store, _ = self._setup()

        async def _never(*_: object, **__: object) -> None:
            await asyncio.Event().wait()

        store.keep_capture_state = AsyncMock(side_effect=_never)

        await pool.capture_media("https://dr0pstream.com/e/abc", timeout=10)
        await asyncio.sleep(0)
        (task,) = pool._keep_tasks
        await pool.cleanup()

        assert task.cancelled()
        assert not pool._keep_tasks


# ------------------------------------------------------------------
# The warm-up at the app's start (keep-capture-state, row 93)
# ------------------------------------------------------------------


class TestWarmup:
    async def test_the_warmup_creates_the_context_a_capture_reuses(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()
        pool = StealthPool(browser_pool=shared_pool)

        await pool.warmup()

        assert await pool._ensure_context() is context
        shared_pool.warmup.assert_awaited_once()
        browser.new_context.assert_awaited_once()

    async def test_a_capture_during_the_warmup_waits_for_its_context(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()
        launched = asyncio.Event()

        async def _slow_launch() -> tuple[AsyncMock, MagicMock]:
            await launched.wait()
            return browser, MagicMock()

        shared_pool.warmup = AsyncMock(side_effect=_slow_launch)
        pool = StealthPool(browser_pool=shared_pool)

        warming = asyncio.create_task(pool.warmup())
        await asyncio.sleep(0)
        capture = asyncio.create_task(pool._ensure_context())
        await asyncio.sleep(0)
        assert not capture.done()  # held by the pool's lock
        launched.set()
        await warming

        assert await capture is context
        shared_pool.warmup.assert_awaited_once()
        browser.new_context.assert_awaited_once()

    async def test_a_failed_warmup_is_logged_and_the_capture_launches(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        working = shared_pool.warmup
        shared_pool.warmup = AsyncMock(side_effect=PlaywrightError("launch failed"))
        pool = StealthPool(browser_pool=shared_pool)

        with structlog.testing.capture_logs() as logs:
            await pool.warmup()  # does not raise

        (failed,) = [e for e in logs if e["event"] == "browser_warmup_failed"]
        assert failed["error"] == "Error"
        shared_pool.warmup = working
        assert await pool._ensure_context() is context
        working.assert_awaited_once()

    async def test_the_warmup_starts_from_the_kept_state(self) -> None:
        shared_pool, browser, _ = _mock_pool_stack()
        store = AsyncMock()
        state = {"cookies": [], "origins": []}
        store.creation_state = AsyncMock(return_value=state)

        await StealthPool(browser_pool=shared_pool, clearance_store=store).warmup()

        browser.new_context.assert_awaited_once_with(
            service_workers="block", storage_state=state
        )

    async def test_a_cancelled_warmup_ends(self) -> None:
        shared_pool, _, _ = _mock_pool_stack()

        async def _never() -> None:
            await asyncio.Event().wait()

        shared_pool.warmup = AsyncMock(side_effect=_never)
        warming = asyncio.create_task(StealthPool(browser_pool=shared_pool).warmup())
        await asyncio.sleep(0)

        warming.cancel()
        with pytest.raises(asyncio.CancelledError):
            await warming
