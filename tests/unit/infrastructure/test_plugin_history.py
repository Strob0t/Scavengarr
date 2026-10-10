"""Tests for the plugins' long-term record (ideas backlog, N2)."""

from __future__ import annotations

import asyncio
import pickle
from typing import Any
from unittest.mock import AsyncMock

import structlog
from structlog.typing import EventDict

from scavengarr.infrastructure.plugins.history import PluginHistory

_KEY = "plugin_history:v1"
_DAY_S = 86400
# 2026-10-07 12:00:00 UTC
_NOON = 1791374400.0


def _backend() -> AsyncMock:
    """A CachePort over a dict that pickles like both adapters."""
    data: dict[str, bytes] = {}
    cache = AsyncMock()

    async def get(key: str) -> Any:
        return pickle.loads(data[key]) if key in data else None

    async def set_(key: str, value: Any, *, ttl: int | None = None) -> None:
        data[key] = pickle.dumps(value)

    async def delete(key: str) -> bool:
        return data.pop(key, None) is not None

    cache.get.side_effect = get
    cache.set.side_effect = set_
    cache.delete.side_effect = delete
    cache.data = data
    return cache


class _Clock:
    def __init__(self, now: float = _NOON) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _history(
    cache: AsyncMock | None = None,
    clock: _Clock | None = None,
    *,
    interval_s: float = 60.0,
) -> PluginHistory:
    return PluginHistory(
        cache or _backend(), interval_s=interval_s, clock=clock or _Clock()
    )


def _events(logs: list[EventDict], name: str) -> list[EventDict]:
    return [e for e in logs if e["event"] == name]


class TestCounters:
    def test_counts_per_plugin_and_day(self) -> None:
        history = _history()

        history.count("kinoger", "searches")
        history.count("kinoger", "searches")
        history.count("kinoger", "results", 7)
        history.count("sto", "timeouts")

        report = history.report()
        assert report["today"] == "2026-10-07"
        assert report["window_days"] == 180
        assert report["plugins"]["kinoger"]["days"] == {
            "2026-10-07": {"searches": 2, "results": 7}
        }
        assert report["plugins"]["sto"]["days"] == {"2026-10-07": {"timeouts": 1}}

    def test_challenges_are_a_counter(self) -> None:
        """A search in which the site showed a challenge (step 50)."""
        history = _history()

        history.count("kinoger", "searches")
        history.count("kinoger", "challenges")

        assert history.report()["plugins"]["kinoger"]["days"] == {
            "2026-10-07": {"searches": 1, "challenges": 1}
        }

    def test_the_day_changes_at_midnight_utc(self) -> None:
        clock = _Clock(_NOON + 12 * 3600 - 1)
        history = _history(clock=clock)

        history.count("sto", "searches")
        clock.now += 1
        history.count("sto", "searches")

        assert history.report()["plugins"]["sto"]["days"] == {
            "2026-10-07": {"searches": 1},
            "2026-10-08": {"searches": 1},
        }

    def test_the_last_day_with_a_result(self) -> None:
        clock = _Clock()
        history = _history(clock=clock)
        history.count("sto", "results", 3)
        clock.now += 2 * _DAY_S
        history.count("sto", "searches")
        history.count("kinox", "searches")

        plugins = history.report()["plugins"]
        assert plugins["sto"]["last_result_day"] == "2026-10-07"
        assert plugins["kinox"]["last_result_day"] is None

    def test_the_unreachable_share_per_window(self) -> None:
        clock = _Clock()
        history = _history(clock=clock)
        # 100 days ago: 2 checks, both unreachable; 40 days ago: 1 of 2;
        # today: 4 checks, none unreachable
        clock.now = _NOON - 100 * _DAY_S
        history.count("movie4k", "checks", 2)
        history.count("movie4k", "unreachable", 2)
        clock.now = _NOON - 40 * _DAY_S
        history.count("movie4k", "checks", 2)
        history.count("movie4k", "unreachable", 1)
        clock.now = _NOON
        history.count("movie4k", "checks", 4)
        history.count("sto", "searches")

        plugins = history.report()["plugins"]
        assert plugins["movie4k"]["unreachable_share"] == {
            "30": 0.0,
            "90": 0.1667,
            "180": 0.375,
        }
        assert plugins["sto"]["unreachable_share"] == {
            "30": None,
            "90": None,
            "180": None,
        }

    def test_the_window_ends_179_days_back(self) -> None:
        clock = _Clock()
        history = _history(clock=clock)
        clock.now = _NOON - 180 * _DAY_S
        history.count("old", "searches")
        clock.now = _NOON - 179 * _DAY_S
        history.count("kept", "searches")
        clock.now = _NOON

        assert sorted(history.report()["plugins"]) == ["kept"]


class TestRestart:
    async def test_the_record_survives_a_restart(self) -> None:
        cache = _backend()
        old = _history(cache)
        await old.restore()
        old.count("sto", "results", 2)
        await old.aclose()

        new = _history(cache)
        with structlog.testing.capture_logs() as logs:
            await new.restore()

        assert new.report()["plugins"]["sto"]["days"] == {"2026-10-07": {"results": 2}}
        [event] = _events(logs, "plugin_history_restored")
        assert (event["plugins"], event["days"]) == (1, 1)

    async def test_first_start_restores_nothing(self) -> None:
        history = _history()
        with structlog.testing.capture_logs() as logs:
            await history.restore()

        [event] = _events(logs, "plugin_history_restored")
        assert (event["plugins"], event["days"]) == (0, 0)
        assert not _events(logs, "plugin_history_discarded")

    async def test_old_days_are_dropped_at_the_restore(self) -> None:
        cache = _backend()
        clock = _Clock(_NOON - 200 * _DAY_S)
        old = _history(cache, clock)
        old.count("gone", "searches")
        await old.aclose()

        new = _history(cache, _Clock())
        await new.restore()

        assert new.report()["plugins"] == {}

    async def test_another_version_is_deleted(self) -> None:
        cache = _backend()
        await cache.set(_KEY, {"version": 2, "plugins": {}})
        history = _history(cache)
        with structlog.testing.capture_logs() as logs:
            await history.restore()

        [event] = _events(logs, "plugin_history_discarded")
        assert event["reason"] == "version"
        assert await cache.get(_KEY) is None

    async def test_a_malformed_record_is_deleted(self) -> None:
        cache = _backend()
        await cache.set(_KEY, {"version": 1, "plugins": {"sto": [1, 2]}})
        history = _history(cache)
        with structlog.testing.capture_logs() as logs:
            await history.restore()

        [event] = _events(logs, "plugin_history_discarded")
        assert event["reason"] == "malformed"
        assert await cache.get(_KEY) is None

    async def test_an_unreadable_record_is_deleted(self) -> None:
        cache = _backend()
        cache.get.side_effect = pickle.UnpicklingError("cut")
        history = _history(cache)
        with structlog.testing.capture_logs() as logs:
            await history.restore()

        [event] = _events(logs, "plugin_history_discarded")
        assert event["reason"] == "unreadable"
        cache.delete.assert_awaited_once_with(_KEY)

    async def test_a_backend_that_fails_does_not_stop_the_start(self) -> None:
        cache = _backend()
        cache.get.side_effect = ConnectionError("redis down")
        cache.delete.side_effect = ConnectionError("redis down")
        history = _history(cache)
        with structlog.testing.capture_logs() as logs:
            await history.restore()

        assert _events(logs, "plugin_history_restore_failed")
        history.count("sto", "searches")


class TestWrites:
    async def test_no_write_while_nothing_changed(self) -> None:
        cache = _backend()
        history = _history(cache)
        await history.restore()

        await history.aclose()

        cache.set.assert_not_awaited()

    async def test_nothing_new_after_a_restore(self) -> None:
        cache = _backend()
        old = _history(cache)
        old.count("sto", "searches")
        await old.aclose()
        cache.set.reset_mock()

        new = _history(cache)
        await new.restore()
        await new.aclose()

        cache.set.assert_not_awaited()

    async def test_a_count_is_written_within_the_interval(self) -> None:
        cache = _backend()
        history = _history(cache, interval_s=0.01)
        await history.restore()
        written = asyncio.Event()
        cache.set.side_effect = lambda *args, **kwargs: written.set()
        history.start()

        history.count("sto", "searches")
        await asyncio.wait_for(written.wait(), timeout=2)
        await history.aclose()

        key, record = cache.set.await_args_list[0].args
        assert key == _KEY
        assert record == {
            "version": 1,
            "plugins": {"sto": {"2026-10-07": {"searches": 1}}},
        }
        assert cache.set.await_args_list[0].kwargs["ttl"] > 180 * _DAY_S

    async def test_aclose_writes_the_last_counts(self) -> None:
        cache = _backend()
        history = _history(cache)
        await history.restore()
        history.start()

        history.count("sto", "checks")
        await history.aclose()

        assert (await cache.get(_KEY))["plugins"] == {
            "sto": {"2026-10-07": {"checks": 1}}
        }

    async def test_days_older_than_the_window_are_dropped_at_the_write(self) -> None:
        cache = _backend()
        clock = _Clock(_NOON - 180 * _DAY_S)
        history = _history(cache, clock)
        history.count("sto", "searches")
        clock.now = _NOON - 179 * _DAY_S
        history.count("sto", "results", 1)
        clock.now = _NOON
        history.count("sto", "searches")

        await history.aclose()

        assert sorted((await cache.get(_KEY))["plugins"]["sto"]) == [
            "2026-04-11",
            "2026-10-07",
        ]

    async def test_a_failed_write_is_tried_again(self) -> None:
        cache = _backend()
        history = _history(cache)
        await history.restore()
        history.count("sto", "searches")
        set_ = cache.set.side_effect
        cache.set.side_effect = OSError("disk full")

        with structlog.testing.capture_logs() as logs:
            await history.aclose()
        cache.set.side_effect = set_
        await history.aclose()

        assert _events(logs, "plugin_history_save_failed")
        assert await cache.get(_KEY) is not None
