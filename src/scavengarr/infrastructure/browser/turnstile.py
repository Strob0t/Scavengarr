"""Solve Cloudflare challenge pages, including the interactive Turnstile.

Managed challenges show a "Verify you are human" checkbox inside a
``challenges.cloudflare.com`` iframe (closed shadow root). It does not clear
on its own; a click is required. Patchright locators reach into the closed
shadow root, and only a headful browser passes the check
(docs/plans/antibot-patchright.md, Phase 0).
"""

from __future__ import annotations

import time

import structlog
from patchright.async_api import Page

log = structlog.get_logger(__name__)

_CHALLENGE_TITLES: tuple[str, ...] = (
    "just a moment",
    "attention required",
    "nur einen moment",
    "einen moment",
)
_CHALLENGE_FRAME = "challenges.cloudflare.com"
# The label wraps the checkbox and comes first in DOM order; clicking it was
# the variant verified live.
_CHECKBOX = "input[type=checkbox], label"

_CLICK_AFTER_S = 3.0  # give non-interactive challenges a chance first
_RECLICK_EVERY_S = 8.0
_POLL_MS = 500
_CLICK_TIMEOUT_MS = 3_000


def _now() -> float:
    return time.monotonic()


async def is_challenge_page(page: Page) -> bool:
    """Return True while *page* shows a Cloudflare challenge title."""
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
            log.info("cloudflare_solved", url=page.url, clicked=last_click is not None)
            return True
