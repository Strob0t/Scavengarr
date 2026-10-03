"""Solve Cloudflare challenge pages, including the interactive Turnstile.

Managed challenges show a "Verify you are human" checkbox inside a
``challenges.cloudflare.com`` iframe (closed shadow root). It does not clear
on its own; a click is required. Patchright locators reach into the closed
shadow root, and only a headful browser passes the check
(docs/plans/antibot-patchright.md, Phase 0).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog
from patchright.async_api import Page

T = TypeVar("T")

log = structlog.get_logger(__name__)

_CHALLENGE_TITLES: tuple[str, ...] = (
    "just a moment",
    "attention required",
    "nur einen moment",
    "einen moment",
    # HostAdmin WAF (kinoger behind Cloudflare, 2026-10-03): a proof of work
    # in a web worker (~1 s), then a redirect titled "Loading <url>"
    "verification...",
    "loading http",
)
_CHALLENGE_FRAME = "challenges.cloudflare.com"
# The label wraps the checkbox and comes first in DOM order; clicking it was
# the variant verified live.
_CHECKBOX = "input[type=checkbox], label"

_CLICK_AFTER_S = 3.0  # give non-interactive challenges a chance first
_RECLICK_EVERY_S = 8.0
_POLL_MS = 500
_CLICK_TIMEOUT_MS = 3_000
_SETTLE_TIMEOUT_MS = 10_000


def _now() -> float:
    return time.monotonic()


async def is_challenge_page(page: Page) -> bool:
    """Return True while *page* shows a challenge title (Cloudflare, WAF)."""
    try:
        title = await page.title()
    except Exception:  # noqa: BLE001  (navigation in progress)
        return False
    if not isinstance(title, str):
        return False
    return any(marker in title.lower() for marker in _CHALLENGE_TITLES)


async def _click_checkbox(page: Page) -> bool:
    for frame in page.frames:
        if _CHALLENGE_FRAME not in frame.url:
            continue
        try:
            await frame.locator(_CHECKBOX).first.click(timeout=_CLICK_TIMEOUT_MS)
            log.debug("turnstile_clicked", url=page.url)
            return True
        except Exception:  # noqa: BLE001  (widget not rendered yet)
            log.debug("turnstile_click_failed", exc_info=True)
    return False


_NAVIGATION_ERRORS = ("execution context was destroyed", "page is navigating")


async def read_when_settled(
    page: Page, read: Callable[[], Awaitable[T]], *, attempts: int = 3
) -> T:
    """Run *read* (e.g. ``page.content``), retrying while *page* navigates.

    Cloudflare can reload the page once more after the challenge cleared,
    without a ``__cf_chl`` marker that ``_settle`` could wait for.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await read()
        except Exception as exc:
            navigating = any(m in str(exc).lower() for m in _NAVIGATION_ERRORS)
            if not navigating or attempt == attempts:
                raise
            log.debug("page_read_retry_navigation", url=page.url, attempt=attempt)
            await page.wait_for_load_state("domcontentloaded")
    raise AssertionError("unreachable")  # pragma: no cover


async def _settle(page: Page) -> None:
    """Wait for the post-challenge redirect that drops ``__cf_chl_tk``.

    The challenge title disappears before that navigation finishes; reading
    the page too early fails with "page is navigating".
    """
    try:
        await page.wait_for_url(
            lambda url: "__cf_chl" not in url,
            timeout=_SETTLE_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        await page.wait_for_load_state("domcontentloaded")
    except Exception:  # noqa: BLE001  (no redirect: page is already final)
        log.debug("cloudflare_settle_timeout", url=page.url)


_WIDGET_TOKEN = "[name='cf-turnstile-response']"
_SUBMIT_JS = "form => form.requestSubmit()"


async def pass_turnstile_widget(page: Page, *, timeout_ms: int) -> bool:
    """Pass a Turnstile widget embedded in a form, then submit the form.

    Sites that gate link-outs (s.to) show the widget in a modal instead of a
    challenge page. Like a page challenge it may clear by itself; otherwise
    the checkbox is ticked (again every ``_RECLICK_EVERY_S``). The widget's
    token lands in a hidden ``cf-turnstile-response`` input of the form.
    Returns False at once when no widget is shown, and when no token
    arrived within *timeout_ms*.
    """
    if not await page.locator(_WIDGET_TOKEN).count():
        return False
    token = page.locator(_WIDGET_TOKEN).first
    start = _now()
    deadline = start + timeout_ms / 1000
    last_click: float | None = None
    while (now := _now()) < deadline:
        if await token.input_value():
            await page.locator(f"form:has({_WIDGET_TOKEN})").first.evaluate(_SUBMIT_JS)
            log.info("turnstile_widget_passed", url=page.url, clicked=bool(last_click))
            return True
        due = last_click is None or now - last_click >= _RECLICK_EVERY_S
        if now - start >= _CLICK_AFTER_S and due and await _click_checkbox(page):
            last_click = now
        await page.wait_for_timeout(_POLL_MS)
    log.info("turnstile_widget_unsolved", url=page.url, timeout_ms=timeout_ms)
    return False


async def solve_cloudflare(page: Page, *, timeout_ms: int) -> bool:
    """Wait for a Cloudflare challenge to clear, clicking Turnstile if needed.

    Returns True when the page is (or becomes) a normal page, False when the
    challenge is still shown after *timeout_ms*.
    """
    if not await is_challenge_page(page):
        return True

    start = _now()
    deadline = start + timeout_ms / 1000
    last_click: float | None = None
    while True:
        now = _now()
        if now >= deadline:
            log.info("cloudflare_unsolved", url=page.url, timeout_ms=timeout_ms)
            return False
        due = last_click is None or now - last_click >= _RECLICK_EVERY_S
        if now - start >= _CLICK_AFTER_S and due and await _click_checkbox(page):
            last_click = now
        await page.wait_for_timeout(_POLL_MS)
        if not await is_challenge_page(page):
            await _settle(page)
            log.info("cloudflare_solved", url=page.url, clicked=last_click is not None)
            return True
