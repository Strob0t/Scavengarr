"""Tests for HosterStateStore (openspec persist-resolver-state)."""

from __future__ import annotations

import asyncio
import pickle
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import structlog
from structlog.typing import EventDict

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.hoster_resolvers.state_store import HosterStateStore

_KEY = "hoster_state:v1"
_ALIVE = "https://voe.sx/e/alive"
_STREAM = ResolvedStream(video_url="https://cdn.example.com/v.mp4")


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


def _resolver() -> MagicMock:
    resolver = MagicMock()
    resolver.name = "voe"
    resolver.resolve = AsyncMock(return_value=_STREAM)
    return resolver


class _Process:
    """One run of the app: a registry, its breakers and their store."""

    def __init__(self, cache: AsyncMock, *, interval_s: float = 30.0) -> None:
        self.registry = HosterResolverRegistry(resolvers=[_resolver()])
        self.hoster_breaker = PluginCircuitBreaker(failure_threshold=1)
        self.plugin_breaker = PluginCircuitBreaker(failure_threshold=1)
        self.store = HosterStateStore(
            cache,
            self.registry,
            {"plugin": self.plugin_breaker, "hoster": self.hoster_breaker},
            interval_s=interval_s,
        )


def _events(logs: list[EventDict], name: str) -> list[EventDict]:
    return [e for e in logs if e["event"] == name]


class TestRestart:
    async def test_the_state_survives_a_restart(self) -> None:
        cache = _backend()
        old = _Process(cache)
        await old.store.restore()
        await old.registry.resolve(_ALIVE)
        old.hoster_breaker.record_failure("filemoon")
        await old.store.aclose()

        new = _Process(cache)
        with structlog.testing.capture_logs() as logs:
            await new.store.restore()

        assert new.registry.cached(_ALIVE) == old.registry.cached(_ALIVE)
        assert new.registry.cached(_ALIVE)[1] is not None
        assert new.hoster_breaker.state("filemoon") == "open"
        assert new.plugin_breaker.export_state() == []
        [event] = _events(logs, "hoster_state_restored")
        assert event["resolutions"] == 1
        assert event["redirects"] == 0
        assert event["breakers"] == 1
        assert event["age_s"] == 0
        assert new.store.restored["resolutions"] == 1
        assert new.store.restored["restored_at"] is not None

    async def test_first_start_restores_nothing(self) -> None:
        process = _Process(_backend())
        with structlog.testing.capture_logs() as logs:
            await process.store.restore()

        [event] = _events(logs, "hoster_state_restored")
        assert (event["resolutions"], event["redirects"], event["breakers"]) == (
            0,
            0,
            0,
        )
        assert not _events(logs, "hoster_state_discarded")

    async def test_a_snapshot_from_the_future_keeps_its_lifetimes(self) -> None:
        """The Pi has no clock until NTP: a negative downtime counts as 0."""
        cache = _backend()
        await cache.set(
            _KEY,
            {
                "version": 1,
                "taken_at": time.time() + 600,
                "results": [
                    {
                        "url": _ALIVE,
                        "stream": None,
                        "remaining": 100.0,
                        "resolver": "voe",
                    }
                ],
                "redirects": [],
                "breakers": {},
            },
        )
        process = _Process(cache)
        await process.store.restore()

        left = process.registry._result_cache[_ALIVE].expires_at - time.monotonic()
        assert 99 < left <= 100


class TestDiscard:
    async def _restore(self, cache: AsyncMock) -> tuple[_Process, list[EventDict]]:
        process = _Process(cache)
        with structlog.testing.capture_logs() as logs:
            await process.store.restore()
        return process, logs

    async def test_another_version_is_deleted(self) -> None:
        cache = _backend()
        await cache.set(
            _KEY,
            {
                "version": 0,
                "taken_at": time.time(),
                "results": [
                    {
                        "url": _ALIVE,
                        "stream": None,
                        "remaining": 100.0,
                        "resolver": "voe",
                    }
                ],
                "redirects": [],
                "breakers": {},
            },
        )

        process, logs = await self._restore(cache)

        [event] = _events(logs, "hoster_state_discarded")
        assert event["reason"] == "version"
        assert _KEY not in cache.data
        assert process.registry.cached(_ALIVE) == (False, None)
        assert not _events(logs, "hoster_state_restored")

    async def test_a_malformed_snapshot_is_deleted(self) -> None:
        cache = _backend()
        await cache.set(
            _KEY,
            {
                "version": 1,
                "taken_at": time.time(),
                "results": [{"url": _ALIVE}],
                "redirects": [],
                "breakers": {},
            },
        )

        _, logs = await self._restore(cache)

        [event] = _events(logs, "hoster_state_discarded")
        assert event["reason"] == "malformed"
        assert _KEY not in cache.data

    async def test_something_else_under_the_key_is_deleted(self) -> None:
        cache = _backend()
        await cache.set(_KEY, "not a snapshot")

        _, logs = await self._restore(cache)

        assert _events(logs, "hoster_state_discarded")[0]["reason"] == "malformed"
        assert _KEY not in cache.data

    async def test_an_unreadable_snapshot_is_deleted(self) -> None:
        """A cut write or another Python: unpickling fails in the backend."""
        cache = _backend()
        cache.data[_KEY] = b"\x80\x05 cut"

        _, logs = await self._restore(cache)

        assert _events(logs, "hoster_state_discarded")[0]["reason"] == "unreadable"
        assert _KEY not in cache.data

    async def test_a_backend_that_fails_does_not_stop_the_start(self) -> None:
        cache = _backend()
        cache.get.side_effect = OSError("disk I/O error")
        cache.delete.side_effect = OSError("disk I/O error")

        _, logs = await self._restore(cache)

        assert _events(logs, "hoster_state_restore_failed")


class TestWrites:
    async def test_no_write_while_nothing_changed(self) -> None:
        cache = _backend()
        process = _Process(cache)
        await process.store.restore()

        await process.store.aclose()

        cache.set.assert_not_awaited()

    async def test_nothing_new_after_a_restore(self) -> None:
        """The restored state is the stored one: no write until a change."""
        cache = _backend()
        old = _Process(cache)
        await old.registry.resolve(_ALIVE)
        await old.store.aclose()
        cache.set.reset_mock()

        new = _Process(cache)
        await new.store.restore()
        await new.store.aclose()

        cache.set.assert_not_awaited()

    async def test_a_change_is_written_within_the_interval(self) -> None:
        cache = _backend()
        process = _Process(cache, interval_s=0.01)
        await process.store.restore()
        written = asyncio.Event()
        cache.set.side_effect = lambda *args, **kwargs: written.set()
        process.store.start()

        process.hoster_breaker.record_failure("filemoon")
        await asyncio.wait_for(written.wait(), timeout=2)
        await process.store.aclose()

        key, snapshot = cache.set.await_args_list[0].args
        assert key == _KEY
        assert snapshot["version"] == 1
        assert [e["name"] for e in snapshot["breakers"]["hoster"]] == ["filemoon"]
        assert cache.set.await_args_list[0].kwargs["ttl"] > 3600

    async def test_aclose_writes_the_last_changes(self) -> None:
        cache = _backend()
        process = _Process(cache)
        await process.store.restore()
        process.store.start()

        await process.registry.resolve(_ALIVE)
        await process.store.aclose()

        assert [e["url"] for e in (await cache.get(_KEY))["results"]] == [_ALIVE]

    async def test_a_failed_write_is_tried_again(self) -> None:
        cache = _backend()
        process = _Process(cache)
        await process.store.restore()
        await process.registry.resolve(_ALIVE)
        set_ = cache.set.side_effect
        cache.set.side_effect = OSError("disk full")

        with structlog.testing.capture_logs() as logs:
            await process.store.aclose()
        cache.set.side_effect = set_
        await process.store.aclose()

        assert _events(logs, "hoster_state_save_failed")
        assert await cache.get(_KEY) is not None
