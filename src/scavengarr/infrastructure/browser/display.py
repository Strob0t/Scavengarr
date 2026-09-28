"""Headful vs. headless decision for Chromium launches.

Interactive Cloudflare Turnstile rejects every headless browser, while a
headful Patchright Chromium passes (docs/plans/antibot-patchright.md).
Headful needs an X display (``DISPLAY``, e.g. Xvfb); without one the launch
would fail, so we fall back to headless and warn once.
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

_warned_no_display = False


def resolve_headless(requested: bool) -> bool:
    """Return the effective ``headless`` flag for ``chromium.launch()``."""
    global _warned_no_display  # noqa: PLW0603
    if requested:
        return True
    if os.environ.get("DISPLAY"):
        return False
    if not _warned_no_display:
        _warned_no_display = True
        log.warning(
            "browser_headful_no_display",
            hint="start under Xvfb (xvfb-run -a) or set playwright.headless=true",
        )
    return True
