"""Shared fixtures for live plugin smoke tests.

These tests hit real websites — network errors and Cloudflare blocks
are handled gracefully via pytest.skip().
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scavengarr.infrastructure.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Auth env-var registry
# ---------------------------------------------------------------------------

AUTH_ENV_VARS: dict[str, tuple[str, str]] = {
    "boerse": ("SCAVENGARR_BOERSE_USERNAME", "SCAVENGARR_BOERSE_PASSWORD"),
    "mygully": ("SCAVENGARR_MYGULLY_USERNAME", "SCAVENGARR_MYGULLY_PASSWORD"),
    "dataload": ("SCAVENGARR_DATALOAD_USERNAME", "SCAVENGARR_DATALOAD_PASSWORD"),
    "myboerse": ("SCAVENGARR_MYBOERSE_USERNAME", "SCAVENGARR_MYBOERSE_PASSWORD"),
}


def has_auth(plugin_name: str) -> bool:
    """Check if env vars are set for an auth-required plugin."""
    env_vars = AUTH_ENV_VARS.get(plugin_name)
    if env_vars is None:
        return True
    user_var, pass_var = env_vars
    return bool(os.environ.get(user_var)) and bool(os.environ.get(pass_var))


# ---------------------------------------------------------------------------
# Chromium availability check (cached)
# ---------------------------------------------------------------------------

_CHROMIUM_AVAILABLE: bool | None = None


async def chromium_available() -> bool:
    """Check if Playwright Chromium is installed (result is cached).

    Async on purpose: the smoke tests run inside an event loop, where the
    sync API raises and the check would always report "not installed".
    """
    global _CHROMIUM_AVAILABLE  # noqa: PLW0603
    if _CHROMIUM_AVAILABLE is None:
        try:
            from patchright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                await browser.close()
            _CHROMIUM_AVAILABLE = True
        except Exception:
            _CHROMIUM_AVAILABLE = False
    return _CHROMIUM_AVAILABLE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def plugin_registry() -> PluginRegistry:
    """Real plugin registry pointing at the plugins/ directory."""
    plugin_dir = Path(__file__).resolve().parents[2] / "plugins"
    registry = PluginRegistry(plugin_dir)
    registry.discover()
    return registry
