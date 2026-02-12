"""Tests for PlaywrightPluginBase shared base class."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase

# ---------------------------------------------------------------------------
# Concrete test subclass
# ---------------------------------------------------------------------------


class _TestPlugin(PlaywrightPluginBase):
    name = "test-pw"
    provides = "stream"
    _domains = ["example.com", "fallback.com"]


class _SingleDomainPlugin(PlaywrightPluginBase):
    name = "single-pw"
    provides = "download"
    _domains = ["only.com"]


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestInit:
    def test_base_url_set_from_first_domain(self) -> None:
        plugin = _TestPlugin()
        assert plugin.base_url == "https://example.com"

    def test_attributes_set(self) -> None:
        plugin = _TestPlugin()
        assert plugin.name == "test-pw"
        assert plugin.provides == "stream"
        assert plugin.mode == "playwright"
        assert plugin.default_language == "de"

    def test_initial_state_is_none(self) -> None:
        plugin = _TestPlugin()
        assert plugin._pw is None
        assert plugin._browser is None
        assert plugin._context is None
        assert plugin._page is None
        assert plugin._domain_verified is False


# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------


class TestEnsureBrowser:
    @pytest.mark.asyncio
    async def test_launches_browser(self) -> None:
        plugin = _TestPlugin()

        mock_browser = AsyncMock()
        mock_pw = AsyncMock()
        mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

        with patch(
            "scavengarr.infrastructure.plugins.playwright_base.async_playwright"
        ) as mock_apw:
            mock_apw.return_value.start = AsyncMock(return_value=mock_pw)
            browser = await plugin._ensure_browser()

        assert browser is mock_browser
        assert plugin._browser is mock_browser
        assert plugin._pw is mock_pw

    @pytest.mark.asyncio
    async def test_reuses_existing_browser(self) -> None:
        plugin = _TestPlugin()
        mock_browser = AsyncMock()
        plugin._browser = mock_browser

        browser = await plugin._ensure_browser()
        assert browser is mock_browser


# ---------------------------------------------------------------------------
# Context lifecycle
# ---------------------------------------------------------------------------


class TestEnsureContext:
    @pytest.mark.asyncio
    async def test_creates_context(self) -> None:
        plugin = _TestPlugin()
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        plugin._browser = mock_browser

        ctx = await plugin._ensure_context()

        assert ctx is mock_context
        assert plugin._context is mock_context

    @pytest.mark.asyncio
    async def test_reuses_existing_context(self) -> None:
        plugin = _TestPlugin()
        mock_context = AsyncMock()
        plugin._context = mock_context

        ctx = await plugin._ensure_context()
        assert ctx is mock_context


# ---------------------------------------------------------------------------
# Page lifecycle
# ---------------------------------------------------------------------------


class TestEnsurePage:
    @pytest.mark.asyncio
    async def test_creates_page(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        plugin._context = mock_context

        page = await plugin._ensure_page()

        assert page is mock_page
        assert plugin._page is mock_page

    @pytest.mark.asyncio
    async def test_reuses_open_page(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        plugin._page = mock_page
        plugin._context = AsyncMock()

        page = await plugin._ensure_page()
        assert page is mock_page

    @pytest.mark.asyncio
    async def test_recreates_closed_page(self) -> None:
        plugin = _TestPlugin()
        closed_page = AsyncMock()
        closed_page.is_closed = MagicMock(return_value=True)

        new_page = AsyncMock()
        new_page.is_closed = MagicMock(return_value=False)
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=new_page)

        plugin._page = closed_page
        plugin._context = mock_context

        page = await plugin._ensure_page()
        assert page is new_page


# ---------------------------------------------------------------------------
# Domain verification
# ---------------------------------------------------------------------------


class TestVerifyDomain:
    @pytest.mark.asyncio
    async def test_first_domain_reachable(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(return_value=mock_resp)
        plugin._page = mock_page
        plugin._context = AsyncMock()

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "example.com" in plugin.base_url

    @pytest.mark.asyncio
    async def test_fallback_to_second_domain(self) -> None:
        plugin = _TestPlugin()
        fail_resp = MagicMock()
        fail_resp.status = 503

        ok_resp = MagicMock()
        ok_resp.status = 200

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(side_effect=[fail_resp, ok_resp])
        plugin._page = mock_page
        plugin._context = AsyncMock()

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "fallback.com" in plugin.base_url

    @pytest.mark.asyncio
    async def test_all_domains_fail(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(side_effect=Exception("timeout"))
        plugin._page = mock_page
        plugin._context = AsyncMock()

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "example.com" in plugin.base_url  # falls back to primary

    @pytest.mark.asyncio
    async def test_skips_if_already_verified(self) -> None:
        plugin = _TestPlugin()
        plugin._domain_verified = True
        plugin.base_url = "https://custom.domain"

        await plugin._verify_domain()

        assert plugin.base_url == "https://custom.domain"

    @pytest.mark.asyncio
    async def test_single_domain_skips_verification(self) -> None:
        plugin = _SingleDomainPlugin()

        await plugin._verify_domain()

        assert plugin._domain_verified is True


# ---------------------------------------------------------------------------
# _fetch_page_html
# ---------------------------------------------------------------------------


class TestFetchPageHtml:
    @pytest.mark.asyncio
    async def test_returns_html_on_success(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(return_value=mock_resp)
        mock_page.content = AsyncMock(return_value="<html>ok</html>")
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        plugin._context = mock_context

        html = await plugin._fetch_page_html("https://example.com/page")

        assert html == "<html>ok</html>"
        mock_page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_on_error_status(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 500

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(return_value=mock_resp)
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        plugin._context = mock_context

        html = await plugin._fetch_page_html("https://example.com/page")

        assert html == ""
        mock_page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(side_effect=Exception("network error"))
        mock_page.close = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        plugin._context = mock_context

        html = await plugin._fetch_page_html("https://example.com/page")

        assert html == ""
        mock_page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_waits_for_cloudflare(self) -> None:
        """_fetch_page_html calls _wait_for_cloudflare on success."""
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(return_value=mock_resp)
        mock_page.content = AsyncMock(return_value="<html>cf-ok</html>")

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        plugin._context = mock_context

        html = await plugin._fetch_page_html("https://example.com/page")

        assert html == "<html>cf-ok</html>"
        # CF wait calls wait_for_function
        mock_page.wait_for_function.assert_awaited_once()
        # networkidle wait
        mock_page.wait_for_load_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_cf_on_error_status(self) -> None:
        """_fetch_page_html does NOT wait for CF when status >= 400."""
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 403

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.goto = AsyncMock(return_value=mock_resp)

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        plugin._context = mock_context

        html = await plugin._fetch_page_html("https://example.com/page")

        assert html == ""
        mock_page.wait_for_function.assert_not_awaited()


# ---------------------------------------------------------------------------
# _wait_for_cloudflare
# ---------------------------------------------------------------------------


class TestWaitForCloudflare:
    @pytest.mark.asyncio
    async def test_returns_true_when_cf_resolves(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.wait_for_function = AsyncMock()  # resolves immediately

        result = await plugin._wait_for_cloudflare(mock_page)

        assert result is True
        mock_page.wait_for_function.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.wait_for_function = AsyncMock(
            side_effect=Exception("Timeout 15000ms exceeded"),
        )

        result = await plugin._wait_for_cloudflare(mock_page)

        assert result is False

    @pytest.mark.asyncio
    async def test_uses_configurable_timeout(self) -> None:
        plugin = _TestPlugin()
        plugin._cf_timeout_ms = 5_000
        mock_page = AsyncMock()
        mock_page.wait_for_function = AsyncMock()

        await plugin._wait_for_cloudflare(mock_page)

        call_kwargs = mock_page.wait_for_function.call_args
        assert call_kwargs[1]["timeout"] == 5_000

    @pytest.mark.asyncio
    async def test_checks_just_a_moment_title(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.wait_for_function = AsyncMock()

        await plugin._wait_for_cloudflare(mock_page)

        js_code = mock_page.wait_for_function.call_args[0][0]
        assert "Just a moment" in js_code


# ---------------------------------------------------------------------------
# _navigate_and_wait
# ---------------------------------------------------------------------------


class TestNavigateAndWait:
    @pytest.mark.asyncio
    async def test_success_with_cf_and_idle(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_resp)

        result = await plugin._navigate_and_wait(mock_page, "https://example.com")

        assert result is True
        mock_page.goto.assert_awaited_once()
        mock_page.wait_for_function.assert_awaited_once()  # CF wait
        mock_page.wait_for_load_state.assert_awaited_once()  # networkidle

    @pytest.mark.asyncio
    async def test_returns_false_on_error_status(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 503

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_resp)

        result = await plugin._navigate_and_wait(mock_page, "https://example.com")

        assert result is False
        mock_page.wait_for_function.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_cf_wait(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_resp)

        result = await plugin._navigate_and_wait(
            mock_page, "https://example.com", wait_for_cf=False
        )

        assert result is True
        mock_page.wait_for_function.assert_not_awaited()
        mock_page.wait_for_load_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skip_idle_wait(self) -> None:
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_resp)

        result = await plugin._navigate_and_wait(
            mock_page, "https://example.com", wait_for_idle=False
        )

        assert result is True
        mock_page.wait_for_function.assert_awaited_once()
        mock_page.wait_for_load_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_networkidle_timeout_ignored(self) -> None:
        """networkidle timeout should not cause failure."""
        plugin = _TestPlugin()
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_resp)
        mock_page.wait_for_load_state = AsyncMock(
            side_effect=Exception("Timeout"),
        )

        result = await plugin._navigate_and_wait(mock_page, "https://example.com")

        assert result is True  # networkidle failure is non-fatal

    @pytest.mark.asyncio
    async def test_goto_exception_propagates(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Navigation failed"))

        with pytest.raises(Exception, match="Navigation failed"):
            await plugin._navigate_and_wait(mock_page, "https://example.com")

    @pytest.mark.asyncio
    async def test_uses_configurable_networkidle_timeout(self) -> None:
        plugin = _TestPlugin()
        plugin._networkidle_timeout_ms = 5_000
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=mock_resp)

        await plugin._navigate_and_wait(mock_page, "https://example.com")

        call_kwargs = mock_page.wait_for_load_state.call_args
        assert call_kwargs[1]["timeout"] == 5_000

    @pytest.mark.asyncio
    async def test_none_response_proceeds(self) -> None:
        """page.goto can return None for some navigation types."""
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.goto = AsyncMock(return_value=None)

        result = await plugin._navigate_and_wait(mock_page, "about:blank")

        assert result is True  # None response is acceptable


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_closes_all_resources(self) -> None:
        plugin = _TestPlugin()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_pw = AsyncMock()

        plugin._page = mock_page
        plugin._context = mock_context
        plugin._browser = mock_browser
        plugin._pw = mock_pw
        plugin._domain_verified = True

        await plugin.cleanup()

        mock_page.close.assert_awaited_once()
        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert plugin._page is None
        assert plugin._context is None
        assert plugin._browser is None
        assert plugin._pw is None
        assert plugin._domain_verified is False

    @pytest.mark.asyncio
    async def test_noop_when_no_resources(self) -> None:
        plugin = _TestPlugin()
        await plugin.cleanup()  # Should not raise


# ---------------------------------------------------------------------------
# _new_semaphore
# ---------------------------------------------------------------------------


class TestNewSemaphore:
    def test_returns_semaphore_with_default_limit(self) -> None:
        plugin = _TestPlugin()
        sem = plugin._new_semaphore()
        assert isinstance(sem, asyncio.Semaphore)

    def test_custom_limit(self) -> None:
        plugin = _TestPlugin()
        plugin._max_concurrent = 5
        sem = plugin._new_semaphore()
        assert isinstance(sem, asyncio.Semaphore)


# ---------------------------------------------------------------------------
# search() abstract
# ---------------------------------------------------------------------------


class TestSearchAbstract:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self) -> None:
        plugin = _TestPlugin()
        with pytest.raises(NotImplementedError, match="search.*not implemented"):
            await plugin.search("test")
