"""Tests for the periodic reachability checks of the plugins' sites."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from scavengarr.domain.ports.plugin_history import (
    NO_PLUGIN_HISTORY,
    PluginHistoryPort,
)
from scavengarr.infrastructure.plugins import health_monitor
from scavengarr.infrastructure.plugins.health_monitor import PluginHealthMonitor
from scavengarr.infrastructure.scoring.health_prober import HealthProber

_UP = "https://up.test/"
_SITE = "https://site.test/"


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_monitor, "_CONFIRM_S", 0.0)


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _monitor(
    client: httpx.AsyncClient,
    domains: dict[str, list[str]],
    interval_s: float = 1800.0,
    history: PluginHistoryPort = NO_PLUGIN_HISTORY,
    clock: Callable[[], float] | None = None,
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
        history=history,
        clock=clock or _Clock(),
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


class TestRecord:
    """Every check's verdict goes into the plugins' long-term record."""

    @respx.mock
    async def test_checks_and_unreachable_marks_are_counted(self) -> None:
        respx.head(_UP).respond(200)
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        history = MagicMock(spec=["count"])
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client, {"up": ["up.test"], "site": ["site.test"]}, history=history
            )

            await monitor.check(["up", "site"])

        assert sorted(c.args for c in history.count.call_args_list) == [
            ("site", "checks"),
            ("site", "unreachable"),
            ("up", "checks"),
        ]

    @respx.mock
    async def test_a_check_without_any_answer_counts_nothing(self) -> None:
        respx.head(_UP).mock(side_effect=httpx.ConnectError("down"))
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        history = MagicMock(spec=["count"])
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client, {"up": ["up.test"], "site": ["site.test"]}, history=history
            )

            await monitor.check(["up", "site"])

        history.count.assert_not_called()


class TestStates:
    @respx.mock
    async def test_one_failed_check_marks_and_one_answer_recovers(self) -> None:
        respx.head(_UP).respond(200)
        site = respx.head(_SITE)
        site.side_effect = [
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
            httpx.Response(200),
        ]
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
    async def test_a_site_answering_the_retry_stays_reachable(self) -> None:
        """One try without an answer marked a site down until the next check
        5 minutes later (movie2k in production: no answer within 5 s, an
        answer at the next check; 2026-10-06)."""
        respx.head(_UP).respond(200)
        site = respx.head(_SITE)
        site.side_effect = [httpx.ReadTimeout("slow"), httpx.Response(200)]
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"up": ["up.test"], "site": ["site.test"]})

            await monitor.check(["up", "site"])

        assert monitor.is_reachable("site")
        assert site.call_count == 2

    @respx.mock
    async def test_an_unreachable_site_is_not_tried_twice(self) -> None:
        respx.head(_UP).respond(200)
        site = respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"up": ["up.test"], "site": ["site.test"]})
            await monitor.check(["up", "site"])
            site.reset()

            await monitor.check(["up", "site"])

        assert not monitor.is_reachable("site")
        assert site.call_count == 1

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


class TestMarkedBySearch:
    """A search whose domain check found no domain marks the plugin itself
    (ideas backlog step 21, group 6)."""

    @respx.mock
    async def test_a_mark_skips_the_plugin_until_the_recheck(self) -> None:
        respx.head(_SITE).respond(200)
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"site": ["site.test"]})
            with capture_logs() as logs:
                monitor.mark_unreachable("site")
                monitor.mark_unreachable("site")
                assert not monitor.is_reachable("site")
                await monitor.check(["site"])

        assert monitor.is_reachable("site")
        marks = [e for e in logs if e["event"] == "plugin_unreachable"]
        assert [(e["plugin"], e["source"]) for e in marks] == [("site", "search")]
        assert [e["plugin"] for e in logs if e["event"] == "plugin_reachable"] == [
            "site"
        ]


class TestSchedule:
    @respx.mock
    async def test_all_every_interval_the_unreachable_in_between(self) -> None:
        respx.head(_UP).respond(200)
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        clock = _Clock(1000.0)
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client,
                {"up": ["up.test"], "site": ["site.test"]},
                interval_s=1800,
                clock=clock,
            )
            assert monitor._due(1000.0) == ["site", "up"]
            await monitor.check(["site", "up"])

            # Due within the next half tick counts as due (a tick is 300 s)
            assert monitor._due(1150.0) == []
            assert monitor._due(1151.0) == ["site"]
            assert monitor._due(1300.0) == ["site"]
            assert monitor._due(2800.0) == ["site", "up"]

    @respx.mock
    async def test_the_recheck_pause_doubles_up_to_the_interval(self) -> None:
        """5 min after the failed check, 10, 20, then the interval: a dead
        site costs one check per interval, the full one."""
        respx.head(_UP).respond(200)
        respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        clock = _Clock(0.0)
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client, {"up": ["up.test"], "site": ["site.test"]}, clock=clock
            )
            checks: list[float] = []
            for clock.now in (0.0, 300.0, 600.0, 900.0, 1200.0, 1500.0, 1800.0):
                due = monitor._due(clock.now)
                if due:
                    checks.append(clock.now)
                    await monitor.check(due)
            # The full check at 1800 capped the pause at the interval
            for clock.now in (2100.0, 2400.0, 2700.0, 3000.0, 3300.0):
                assert monitor._due(clock.now) == [], clock.now
            clock.now = 3600.0
            assert monitor._due(3600.0) == ["site", "up"]

        assert checks == [0.0, 300.0, 900.0, 1800.0]

    @respx.mock
    async def test_an_answer_resets_the_pause(self) -> None:
        respx.head(_UP).respond(200)
        site = respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        clock = _Clock(0.0)
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client, {"up": ["up.test"], "site": ["site.test"]}, clock=clock
            )
            await monitor.check(monitor._due(0.0))  # pause 300
            clock.now = 300.0
            await monitor.check(monitor._due(300.0))  # pause 600
            site.side_effect = None
            site.return_value = httpx.Response(200)
            clock.now = 900.0
            await monitor.check(monitor._due(900.0))
            assert monitor.is_reachable("site")
            assert monitor._due(1500.0) == []
            # Down again at the next full check: the pause starts over
            site.side_effect = httpx.ConnectError("down")
            clock.now = 1800.0
            assert monitor._due(1800.0) == ["site", "up"]
            await monitor.check(["site", "up"])

            assert monitor._due(1950.0) == []
            assert monitor._due(2100.0) == ["site"]

    @respx.mock
    async def test_a_search_mark_is_rechecked_after_the_first_pause(self) -> None:
        respx.head(_SITE).respond(200)
        clock = _Clock(0.0)
        async with httpx.AsyncClient() as client:
            monitor = _monitor(client, {"site": ["site.test"]}, clock=clock)
            await monitor.check(monitor._due(0.0))
            clock.now = 1000.0
            monitor.mark_unreachable("site")

            assert monitor._due(1150.0) == []
            assert monitor._due(1300.0) == ["site"]

    @respx.mock
    async def test_runs_until_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(health_monitor, "_FIRST_CHECK_S", 0.0)
        monkeypatch.setattr(health_monitor, "_RETRY_S", 0.01)
        respx.head(_UP).respond(200)
        site = respx.head(_SITE).mock(side_effect=httpx.ConnectError("down"))
        async with httpx.AsyncClient() as client:
            monitor = _monitor(
                client, {"up": ["up.test"], "site": ["site.test"]}, clock=time.monotonic
            )
            task = asyncio.create_task(monitor.run_forever())
            while site.call_count < 3:
                await asyncio.sleep(0.01)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not monitor.is_reachable("site")
