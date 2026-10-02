"""Tests for choosing the httpx plugins' browser fetcher in composition."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx

from scavengarr.infrastructure.browser.solver_fetcher import (
    ChainedBrowserFetcher,
    SolverFetcher,
)
from scavengarr.interfaces.composition import build_browser_fetcher

_URL = "http://byparr:8191"


def _config(*, fallback: bool, solver_url: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        playwright_browser_fallback=fallback, playwright_solver_url=solver_url
    )


def test_own_browser_only() -> None:
    stealth = MagicMock()
    fetcher = build_browser_fetcher(
        _config(fallback=True, solver_url=None), stealth, AsyncMock()
    )
    assert fetcher is stealth


def test_nothing_configured() -> None:
    assert (
        build_browser_fetcher(
            _config(fallback=False, solver_url=None), MagicMock(), AsyncMock()
        )
        is None
    )


def test_solver_only() -> None:
    fetcher = build_browser_fetcher(
        _config(fallback=False, solver_url=_URL), MagicMock(), AsyncMock()
    )
    assert isinstance(fetcher, SolverFetcher)


async def test_own_browser_then_solver() -> None:
    stealth = AsyncMock()
    stealth.fetch_text.return_value = None
    client = AsyncMock()
    client.post.side_effect = httpx.ConnectError("solver down")
    fetcher = build_browser_fetcher(
        _config(fallback=True, solver_url=_URL), stealth, client
    )

    assert isinstance(fetcher, ChainedBrowserFetcher)
    assert await fetcher.fetch_text("https://x/", timeout=1) is None
    stealth.fetch_text.assert_awaited_once()
    client.post.assert_awaited_once()
