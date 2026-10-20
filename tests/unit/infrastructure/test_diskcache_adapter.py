"""Tests for the diskcache adapter."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from scavengarr.infrastructure.cache.diskcache_adapter import DiskcacheAdapter


class _Overlap:
    """Wraps a cache method and records how many calls ran at once.

    Each call stays inside for 10 ms, a window for another to come in;
    with *meet* it stays until *meet* calls are inside (at most 2 s), so
    calls that may run in parallel overlap however late a thread starts.
    """

    def __init__(self, method: Any, *, meet: int | None = None) -> None:
        self._method = method
        self._meet = meet
        self._inside = threading.Condition()
        self._running = 0
        self.peak = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with self._inside:
            self._running += 1
            self.peak = max(self.peak, self._running)
            self._inside.notify_all()
        try:
            meet = self._meet
            if meet is None:
                time.sleep(0.01)
            else:
                with self._inside:
                    self._inside.wait_for(lambda: self.peak >= meet, timeout=2)
            return self._method(*args, **kwargs)
        finally:
            with self._inside:
                self._running -= 1


class TestWrites:
    @pytest.mark.asyncio
    async def test_writes_run_one_at_a_time(self, tmp_path: Path) -> None:
        """SQLite has one writer: parallel writes only wait for its lock
        (20 writes took 80-100 ms in parallel, 6 ms one after another)."""
        async with DiskcacheAdapter(directory=tmp_path) as cache:
            assert cache._cache is not None
            overlap = _Overlap(cache._cache.set)
            cache._cache.set = overlap  # type: ignore[method-assign]

            await asyncio.gather(*(cache.set(f"k{i}", i) for i in range(8)))

            assert overlap.peak == 1
            assert [await cache.get(f"k{i}") for i in range(8)] == list(range(8))

    @pytest.mark.asyncio
    async def test_reads_still_run_in_parallel(self, tmp_path: Path) -> None:
        async with DiskcacheAdapter(directory=tmp_path) as cache:
            await cache.set("k", 1)
            assert cache._cache is not None
            overlap = _Overlap(cache._cache.get, meet=2)
            cache._cache.get = overlap  # type: ignore[method-assign]

            assert await asyncio.gather(*(cache.get("k") for _ in range(4))) == [1] * 4

            assert overlap.peak > 1


class TestPing:
    """The readiness probe's cache check: a local cache answers while open."""

    async def test_an_open_cache_answers_and_a_closed_one_not(
        self, tmp_path: Path
    ) -> None:
        async with DiskcacheAdapter(directory=tmp_path) as cache:
            assert await cache.ping() is True

        assert await cache.ping() is False
