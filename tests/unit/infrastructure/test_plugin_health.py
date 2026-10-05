"""Tests for the periodic reachability checks of the plugins' sites."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from scavengarr.infrastructure.plugins import health_monitor
from scavengarr.infrastructure.plugins.health_monitor import PluginHealthMonitor
from scavengarr.infrastructure.scoring.health_prober import HealthProber

_UP = "https://up.test/"
_SITE = "https://site.test/"


def _monitor(
    client: httpx.AsyncClient,
    domains: dict[str, list[str]],
    interval_s: float = 1800.0,
) -> PluginHealthMonitor:
    """Monitor of plugins named after their first domain."""
    plugins = {
        name: SimpleNamespace(base_url=f"https://{hosts[0]}", _domains=hosts)
        for name, hosts in domains.items()
    }
    registry = MagicMock()
    registry.get.side_effect = plugins.__getitem__
    return PluginHealthMonitor(
        prober=HealthProber(http_client=client, timeout=1.0),
        plugins=registry,
        names=sorted(plugins),
        interval_s=interval_s,
    )


class TestClassification:
    @pytest.mark.parametrize(
        ("answer", "reachable"),
        [
            (httpx.Response(200), True),
            (httpx.Response(404), True),
            (httpx.Response(403), True),
            # A challenge page: the site is up behind Cloudflare (kinoger)
            (httpx.Response(403, headers={"cf-ray": "1"}), True),
            (httpx.Response(503, headers={"cf-ray": "1"}), True),
            (httpx.Response(503), False),
            # Cloudflare cannot reach the site (megakino_to, round 4)
            (httpx.Response(522, headers={"cf-ray": "1"}), False),
            (httpx.ConnectError("name does not resolve"), False),
            (httpx.ConnectTimeout("timed out"), False),
            (httpx.ReadTimeout("timed out"), False),
        ],
    )
    @respx.mock
    async def test_answer(
        self, answer: httpx.Response | Exception, reachable: bool
    ) -> None:
        respx.head(_UP).respond(200)
        route = respx.head(_SITE)
        if isinstance(answer, Exception):
            route.side_effect = answer
        else:
            route.return_value = answer
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"up": ["up.test"], "site": ["site.test"]})

            await monitor.check(["up", "site"])

        assert monitor.is_reachable("site") is reachable
        assert monitor.is_reachable("up")


class TestStates:
    @respx.mock
    async def test_one_failed_check_marks_and_one_answer_recovers(self) -> None:
        respx.head(_UP).respond(200)
        site = respx.head(_SITE)
        site.side_effect = [httpx.ConnectError("down"), httpx.Response(200)]
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"up": ["up.test"], "site": ["site.test"]})

            with capture_logs() as logs:
                await monitor.check(["up", "site"])
                assert not monitor.is_reachable("site")
                await monitor.check(["site"])

        assert monitor.is_reachable("site")
        events = [(e["event"], e.get("plugin")) for e in logs]
        assert ("plugin_unreachable", "site") in events
        assert ("plugin_reachable", "site") in events

    @respx.mock
    async def test_another_domain_of_the_plugin_counts(self) -> None:
        """The plugin picks a working domain itself (_verify_domain)."""
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        respx.head("https://site-mirror.test/").respond(200)
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"site": ["site.test", "site-mirror.test"]})

            await monitor.check(["site"])

        assert monitor.is_reachable("site")

    @respx.mock
    async def test_a_check_without_any_answer_changes_nothing(self) -> None:
        """The own network or DNS is down then, not every site."""
        respx.head(_UP).mock(side_effect=httpx.ConnectError("no network"))
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("no network"))
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"up": ["up.test"], "site": ["site.test"]})

            with capture_logs() as logs:
                await monitor.check(["up", "site"])

        assert monitor.is_reachable("up")
        assert monitor.is_reachable("site")
        events = [e["event"] for e in logs]
        assert "plugin_health_no_answer" in events
        assert "plugin_unreachable" not in events


class TestSchedule:
    @respx.mock
    async def test_all_every_interval_the_unreachable_in_between(self) -> None:
        respx.head(_UP).respond(200)
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client, {"up": ["up.test"], "site": ["site.test"]}, interval_s=1800
            )
            assert monitor._due(1000.0) == ["site", "up"]
            await monitor.check(["site", "up"])

            assert monitor._due(1300.0) == ["site"]
            assert monitor._due(2800.0) == ["site", "up"]

    @respx.mock
    async def test_runs_until_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(health_monitor, "_FIRST_CHECK_S", 0.0)
        monkeypatch.setattr(health_monitor, "_RETRY_S", 0.01)
        respx.head(_UP).respond(200)
        site = respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"up": ["up.test"], "site": ["site.test"]})
            task = asyncio.create_task(monitor.run_forever())
            while site.call_count < 3:
                await asyncio.sleep(0.01)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not monitor.is_reachable("site")
