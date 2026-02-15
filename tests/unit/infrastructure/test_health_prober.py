"""Unit tests for HealthProber."""

from __future__ import annotations

import httpx
import respx

from scavengarr.infrastructure.scoring.health_prober import HealthProber

_URL = "https://example.com"


class TestProbe:
    @respx.mock
    async def test_reachable_returns_ok(self) -> None:
        respx.head(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is True
        assert result.http_status == 200
        assert result.duration_ms > 0
        assert result.error_kind is None

    @respx.mock
    async def test_404_returns_not_ok(self) -> None:
        respx.head(_URL).respond(404)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.http_status == 404

    @respx.mock
    async def test_500_returns_not_ok(self) -> None:
        respx.head(_URL).respond(500)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.http_status == 500

    @respx.mock
    async def test_405_falls_back_to_get(self) -> None:
        respx.head(_URL).respond(405)
        respx.get(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is True
        assert result.http_status == 200

    @respx.mock
    async def test_501_falls_back_to_get(self) -> None:
        respx.head(_URL).respond(501)
        respx.get(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is True
        assert result.http_status == 200

    @respx.mock
    async def test_timeout_returns_error(self) -> None:
        respx.head(_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.error_kind == "timeout"
        assert result.http_status is None

    @respx.mock
    async def test_network_error_returns_error(self) -> None:
        respx.head(_URL).mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.ok is False
        assert result.error_kind == "http_error"

    @respx.mock
    async def test_started_at_is_set(self) -> None:
        respx.head(_URL).respond(200)
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            result = await prober.probe(_URL)

        assert result.started_at is not None
        assert result.started_at.tzinfo is not None


class TestProbeAll:
    @respx.mock
    async def test_probes_multiple_plugins(self) -> None:
        respx.head("https://a.com").respond(200)
        respx.head("https://b.com").respond(200)
        respx.head("https://c.com").respond(500)
        plugins = {
            "plugin_a": "https://a.com",
            "plugin_b": "https://b.com",
            "plugin_c": "https://c.com",
        }
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            results = await prober.probe_all(plugins, concurrency=2)

        assert len(results) == 3
        assert results["plugin_a"].ok is True
        assert results["plugin_b"].ok is True
        assert results["plugin_c"].ok is False

    @respx.mock
    async def test_empty_plugins_dict(self) -> None:
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            results = await prober.probe_all({})

        assert results == {}

    @respx.mock
    async def test_respects_concurrency(self) -> None:
        # Just verify it doesn't crash with concurrency=1.
        respx.head("https://a.com").respond(200)
        respx.head("https://b.com").respond(200)
        plugins = {
            "a": "https://a.com",
            "b": "https://b.com",
        }
        async with httpx.AsyncClient() as client:
            prober = HealthProber(http_client=client)
            results = await prober.probe_all(plugins, concurrency=1)

        assert len(results) == 2
