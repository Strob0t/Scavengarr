"""The app's lifespan warms the stealth browser up after it reported ready
(keep-capture-state, row 93)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from scavengarr.infrastructure.browser.stealth_pool import StealthPool
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

_PLUGIN_GLOBALS: tuple[tuple[type, str], ...] = (
    (HttpxPluginBase, "_shared_http_client"),
    (HttpxPluginBase, "_cache"),
    (HttpxPluginBase, "_browser_fetcher"),
    (PlaywrightPluginBase, "_clearance_store"),
    (PlaywrightPluginBase, "_page_timeout_ms"),
)


class _Warmup:
    """Stands in for ``StealthPool.warmup``: notes the app's readiness when
    it starts and runs until it is cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.ready_at_start: bool | None = None
        self.cancelled = False
        self.state: AppState | None = None

    async def __call__(self, _pool: StealthPool) -> None:
        assert self.state is not None
        self.ready_at_start = self.state.graceful_shutdown.is_ready
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def _run_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_Warmup, list[bool]]:
    warmup = _Warmup()
    cancelled_at_cleanup: list[bool] = []
    cleanup = StealthPool.cleanup

    async def _cleanup(pool: StealthPool) -> None:
        cancelled_at_cleanup.append(warmup.cancelled)
        await cleanup(pool)

    async def _warmup(pool: StealthPool) -> None:
        await warmup(pool)

    monkeypatch.setattr(StealthPool, "warmup", _warmup)
    monkeypatch.setattr(StealthPool, "cleanup", _cleanup)
    # The lifespan sets the plugin bases' class globals (the shared client,
    # cache, browser fetcher, clearance store, page timeout) and leaves them
    # on the closed client and cache: restored after the test
    for base, name in _PLUGIN_GLOBALS:
        monkeypatch.setattr(base, name, getattr(base, name))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    config = load_config(
        cli_overrides={
            "plugin_dir": plugins,
            "cache_dir": tmp_path / "cache",
            "scoring": {"enabled": False},
        }
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        warmup.state = cast(AppState, app.state)
        async with asyncio.timeout(5):
            await warmup.started.wait()
        # The app answers while the warm-up still runs
        assert warmup.state.graceful_shutdown.is_ready
    return warmup, cancelled_at_cleanup


async def test_the_warmup_starts_after_ready_and_ends_before_the_pools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warmup, cancelled_at_cleanup = await _run_app(tmp_path, monkeypatch)

    assert warmup.ready_at_start is True
    assert warmup.cancelled
    assert cancelled_at_cleanup == [True]
