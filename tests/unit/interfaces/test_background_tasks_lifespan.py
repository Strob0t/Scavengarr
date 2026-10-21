"""Every long-lived task of the lifespan logs its death (OpenSpec
add-stall-watchdog, group 1): the six of the composition root and the two
writers' own tasks carry the done-callback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast, get_args

import pytest
from structlog.testing import capture_logs

from scavengarr.domain.ports.telemetry import BackgroundTaskName, TelemetryPort
from scavengarr.infrastructure.browser.stealth_pool import StealthPool
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.hoster_resolvers import state_store
from scavengarr.infrastructure.hoster_resolvers.pass_keeper import PassKeeper
from scavengarr.infrastructure.plugins import history
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase
from scavengarr.infrastructure.plugins.playwright_base import PlaywrightPluginBase
from scavengarr.infrastructure.telemetry.background import watch_task
from scavengarr.interfaces import composition
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

_PLUGIN_GLOBALS: tuple[tuple[type, str], ...] = (
    (HttpxPluginBase, "_shared_http_client"),
    (HttpxPluginBase, "_cache"),
    (HttpxPluginBase, "_browser_fetcher"),
    (PlaywrightPluginBase, "_clearance_store"),
    (PlaywrightPluginBase, "_page_timeout_ms"),
)


async def test_every_long_lived_task_carries_the_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lifespan sets the plugin bases' class globals and leaves them on
    # the closed client and cache: restored after the test
    for base, name in _PLUGIN_GLOBALS:
        monkeypatch.setattr(base, name, getattr(base, name))
    watched: dict[str, asyncio.Task[None]] = {}

    def _watch(
        task: asyncio.Task[None], name: BackgroundTaskName, telemetry: TelemetryPort
    ) -> asyncio.Task[None]:
        assert name not in watched
        watched[name] = task
        return watch_task(task, name, telemetry)

    for module in (composition, state_store, history):
        monkeypatch.setattr(module, "watch_task", _watch)

    async def _warmup(_pool: StealthPool) -> None:
        return None

    monkeypatch.setattr(StealthPool, "warmup", _warmup)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    config = load_config(
        cli_overrides={
            "plugin_dir": plugins,
            "cache_dir": tmp_path / "cache",
            "scoring": {"enabled": True},
        }
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        state = cast(AppState, app.state)
        kept = {
            "loop_lag": state._loop_lag_task,
            "page_budget": state._page_budget_task,
            "plugin_health": state._plugin_health_task,
            "scoring": state._scoring_task,
            "browser_warmup": state._browser_warmup_task,
            "pass_keeper": state._pass_keeper_task,
            "hoster_state_store": state.hoster_state_store._task,
            "plugin_history": state.plugin_history._task,
        }

        assert set(watched) == set(get_args(BackgroundTaskName))
        assert all(watched[name] is task for name, task in kept.items())


async def test_a_task_that_died_before_the_shutdown_does_not_stop_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first task the shutdown stops (the pass keeper) and the last (the
    warm-up) died before it: the cache still closes (row 158)."""
    for base, name in _PLUGIN_GLOBALS:
        monkeypatch.setattr(base, name, getattr(base, name))

    async def _dies(_self: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(StealthPool, "warmup", _dies)
    monkeypatch.setattr(PassKeeper, "run_forever", _dies)
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
    with capture_logs() as logs:
        async with app.router.lifespan_context(app):
            state = cast(AppState, app.state)
            dead = {state._pass_keeper_task, state._browser_warmup_task}
            async with asyncio.timeout(5):
                await asyncio.wait(dead)

    events = [e["event"] for e in logs]
    died = [e["task"] for e in logs if e["event"] == "background_task_died"]
    assert sorted(died) == ["browser_warmup", "pass_keeper"]
    assert "cache_closed" in events
    assert events[-1] == "app_shutdown_complete"
