"""Waiting for a condition instead of the clock.

A test that asserts what happened while it waited polls for it with
``eventually()``: a fixed sleep is either too short on a loaded runner or
longer than the work needs.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


async def eventually(check: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll *check* every 10 ms until it holds; fail after *timeout* seconds."""
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.01)


def until(check: Callable[[], bool], timeout: float = 2.0) -> None:
    """``eventually()`` for a test without a loop: what another thread does."""
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "condition not met in time"
        time.sleep(0.01)
