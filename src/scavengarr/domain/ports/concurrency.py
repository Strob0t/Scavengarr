"""Concurrency budget ports for cross-request coordination."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class ConcurrencyBudgetPort(Protocol):
    """Per-request concurrency budget handle.

    Provides slot acquisition for httpx and Playwright work,
    enforcing fair-share limits relative to other active requests.
    """

    def acquire_httpx(self) -> AsyncIterator[None]:
        """Acquire one httpx concurrency slot (async context manager)."""
        ...

    def acquire_pw(self) -> AsyncIterator[None]:
        """Acquire one Playwright concurrency slot (async context manager)."""
        ...


@runtime_checkable
class ConcurrencyPoolPort(Protocol):
    """Global concurrency pool managing httpx and Playwright slot budgets.

    Each call to ``request()`` returns a per-request budget handle
    that enforces fair-share concurrency across all active requests.
    """

    def request(self) -> AsyncIterator[ConcurrencyBudgetPort]:
        """Enter a request scope, returning a budget handle.

        Async context manager: increments active request count on
        entry, decrements on exit, and notifies waiting requests
        so they can recalculate their fair share.
        """
        ...
