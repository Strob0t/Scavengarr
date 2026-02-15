"""Unit tests for MiniSearchProber."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.scoring.search_prober import (
    MiniSearchProber,
)


def _make_results(count: int = 5) -> list[SearchResult]:
    return [
        SearchResult(
            title=f"Result {i}",
            download_link=f"https://hoster.com/file/{i}",
        )
        for i in range(count)
    ]


def _mock_registry(
    plugin_name: str = "sto",
    results: list[SearchResult] | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock plugin registry with a fake plugin."""
    registry = MagicMock()
    plugin = MagicMock()
    if side_effect:
        plugin.search = AsyncMock(side_effect=side_effect)
    else:
        plugin.search = AsyncMock(return_value=results or [])
    registry.get.return_value = plugin
    return registry


class TestProbe:
    @respx.mock
    async def test_successful_probe(self) -> None:
        results = _make_results(10)
        registry = _mock_registry(results=results)
        for i in range(3):
            respx.head(f"https://hoster.com/file/{i}").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("sto", "Iron Man", 2000)

        assert probe.ok is True
        assert probe.items_found == 10
        assert probe.items_used == 10
        assert probe.hoster_checked == 3
        assert probe.hoster_reachable == 3
        assert probe.duration_ms > 0

    @respx.mock
    async def test_no_results(self) -> None:
        registry = _mock_registry(results=[])
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("sto", "Nonexistent", 2000)

        assert probe.ok is True
        assert probe.items_found == 0
        assert probe.hoster_checked == 0

    @respx.mock
    async def test_plugin_not_found(self) -> None:
        registry = MagicMock()
        registry.get.side_effect = KeyError("unknown")
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("unknown", "query", 2000)

        assert probe.ok is False
        assert probe.error_kind == "plugin_not_found"

    @respx.mock
    async def test_search_error(self) -> None:
        registry = _mock_registry(
            side_effect=RuntimeError("boom")
        )
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.ok is False
        assert probe.error_kind == "search_error"

    @respx.mock
    async def test_max_items_cap(self) -> None:
        results = _make_results(30)
        registry = _mock_registry(results=results)
        for i in range(3):
            respx.head(f"https://hoster.com/file/{i}").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe(
                "sto", "query", 2000, max_items=20
            )

        assert probe.items_found == 30
        assert probe.items_used == 20

    @respx.mock
    async def test_hoster_partial_reachable(self) -> None:
        results = _make_results(5)
        registry = _mock_registry(results=results)
        respx.head("https://hoster.com/file/0").respond(200)
        respx.head("https://hoster.com/file/1").respond(404)
        respx.head("https://hoster.com/file/2").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.hoster_checked == 3
        assert probe.hoster_reachable == 2

    @respx.mock
    async def test_hoster_network_error(self) -> None:
        results = _make_results(5)
        registry = _mock_registry(results=results)
        respx.head("https://hoster.com/file/0").mock(
            side_effect=httpx.ConnectError("refused")
        )
        respx.head("https://hoster.com/file/1").respond(200)
        respx.head("https://hoster.com/file/2").respond(200)

        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.hoster_checked == 3
        assert probe.hoster_reachable == 2

    @respx.mock
    async def test_started_at_is_set(self) -> None:
        registry = _mock_registry(results=[])
        async with httpx.AsyncClient() as client:
            prober = MiniSearchProber(
                plugins=registry, http_client=client
            )
            probe = await prober.probe("sto", "query", 2000)

        assert probe.started_at is not None
        assert probe.started_at.tzinfo is not None
