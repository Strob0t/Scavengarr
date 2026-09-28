"""Tests for StealthPool — Patchright browser pool."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scavengarr.infrastructure.browser.stealth_pool import (
    _BLOCKED_RESOURCE_TYPES,
    _OFFLINE_MARKERS,
    StealthPool,
    _block_resources,
)

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
# _block_resources
# ------------------------------------------------------------------


class TestBlockResources:
    """Route handler blocks heavy resource types."""

    @pytest.mark.parametrize("rtype", sorted(_BLOCKED_RESOURCE_TYPES))
    async def test_blocks_heavy_resource(self, rtype: str) -> None:
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = rtype
        await _block_resources(route)
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()

    @pytest.mark.parametrize("rtype", ["document", "script", "xhr", "fetch"])
    async def test_allows_essential_resources(self, rtype: str) -> None:
        route = AsyncMock()
        route.request = MagicMock()
        route.request.resource_type = rtype
        await _block_resources(route)
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
        browser.new_context.assert_awaited_once()
        context.route.assert_awaited_once()

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
# StealthPool.probe_url
# ------------------------------------------------------------------


class TestStealthPoolProbe:
    """probe_url navigation and classification."""

    async def test_alive_page(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page(html="<html><body>Video Player</body></html>")
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool, timeout_ms=5_000)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is True
        page.goto.assert_awaited_once()
        page.close.assert_awaited_once()

    @pytest.mark.parametrize("marker", _OFFLINE_MARKERS)
    async def test_dead_page_offline_marker(
        self,
        marker: str,
    ) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page(html=f"<html><body>{marker}</body></html>")
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is False

    async def test_navigation_error_returns_false(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_CONNECTION_REFUSED"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc123")

        assert result is False
        page.close.assert_awaited_once()

    async def test_page_closed_even_on_error(self) -> None:
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page()
        page.content = AsyncMock(side_effect=RuntimeError("closed"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc")

        assert result is False
        page.close.assert_awaited_once()

    async def test_cf_wait_timeout_still_checks_content(self) -> None:
        """Even if CF wait times out, content is still checked."""
        shared_pool, browser, context = _mock_pool_stack()

        page = _mock_page(
            html="<html><body>Video Player active</body></html>",
            title="Just a moment...",
        )
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("CF wait"))
        context.new_page = AsyncMock(return_value=page)

        pool = StealthPool(browser_pool=shared_pool)
        result = await pool.probe_url("https://example.com/e/abc")

        # CF wait timed out but page content is alive
        assert result is True


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
        shared_pool, _, context = _mock_pool_stack()
        context.new_page = AsyncMock(
            return_value=_fetch_page(status=404, title="Not Found")
        )

        text = await StealthPool(browser_pool=shared_pool).fetch_text(
            "https://filmfans.org/missing", timeout=10
        )

        assert text is None

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
        pool = StealthPool(browser_pool=shared_pool, fetch_concurrency=2)

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
        page = _fetch_page(title="Too Many Requests")
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


def _request(url: str, *, navigation: bool = True) -> MagicMock:
    request = MagicMock()
    request.url = url
    request.is_navigation_request = MagicMock(return_value=navigation)
    return request


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

        target = await StealthPool(browser_pool=shared_pool).resolve_redirect(
            "https://filmfans.org/external/abc", timeout=10
        )

        assert target == "https://filecrypt.cc/Container/2bdf64bab2.html?mirror=0"
        page.close.assert_awaited_once()

    async def test_same_host_only_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([(["https://filmfans.org/external/abc"], 200)])
        context.new_page = AsyncMock(return_value=page)
        pool = StealthPool(browser_pool=shared_pool)

        assert (
            await pool.resolve_redirect("https://filmfans.org/external/abc", timeout=10)
            is None
        )

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

        assert (
            await pool.resolve_redirect("https://filmfans.org/external/abc", timeout=10)
            is None
        )

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
            target = await StealthPool(browser_pool=shared_pool).resolve_redirect(
                "https://filmfans.org/external/abc", timeout=10
            )

        assert target == "https://filecrypt.cc/Container/x.html"
        mock_sleep.assert_awaited_once_with(2.0)

    async def test_http_error_returns_none(self) -> None:
        shared_pool, _, context = _mock_pool_stack()
        page = _redirect_page([(["https://filmfans.org/external/gone"], 404)])
        page.title = AsyncMock(return_value="Not Found")
        context.new_page = AsyncMock(return_value=page)

        assert (
            await StealthPool(browser_pool=shared_pool).resolve_redirect(
                "https://filmfans.org/external/gone", timeout=10
            )
            is None
        )


# ------------------------------------------------------------------
# capture_media
# ------------------------------------------------------------------


def _player_page(
    *,
    on_load: list[str] | None = None,
    on_click: list[list[str]] | None = None,
    status: int = 200,
) -> MagicMock:
    """Page whose player requests *on_load* URLs while loading and the
    i-th list of *on_click* on the i-th mouse click."""
    page = _mock_page(title="Player")
    listeners: list[object] = []

    def _on(event: str, callback: object) -> None:
        if event == "request":
            listeners.append(callback)

    def _emit(urls: list[str]) -> None:
        for url in urls:
            request = _request(url, navigation=False)
            request.headers = {"referer": "https://player.example/"}
            for callback in listeners:
                callback(request)  # type: ignore[operator]

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
