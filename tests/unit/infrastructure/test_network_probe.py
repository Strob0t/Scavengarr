"""Tests for the readiness probe's network checks (``NetworkProbe``)."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from scavengarr.infrastructure import network_probe
from scavengarr.infrastructure.network_probe import NetworkProbe
from scavengarr.infrastructure.telemetry import Telemetry


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Resolver:
    """The DNS: answers unless *down*; counts its lookups."""

    def __init__(self) -> None:
        self.down = False
        self.names: list[str] = []

    async def __call__(self, name: str) -> object:
        self.names.append(name)
        if self.down:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        return [("addr",)]


class _Rig:
    def __init__(self) -> None:
        self.clock = _Clock()
        self.dns = _Resolver()
        self.cache = AsyncMock()
        self.cache.ping.return_value = True
        self.telemetry = Telemetry()
        self.probe = NetworkProbe(
            self.cache, telemetry=self.telemetry, resolve=self.dns, clock=self.clock
        )

    def down(self, *, cache: bool = True, dns: bool = True) -> None:
        self.cache.ping.return_value = not cache
        self.dns.down = dns

    def gauge(self, check: str) -> float | None:
        return self.telemetry.registry.get_sample_value(
            "scavengarr_network_ok", {"check": check}
        )

    async def lost_after(self, seconds: float, step: float = 30.0) -> list[bool]:
        """Ask every *step* seconds for *seconds* from now, as the image's
        healthcheck does; the verdicts in order."""
        verdicts = [await self.probe.lost()]
        end = self.clock.now + seconds
        while self.clock.now + step <= end:
            self.clock.now += step
            verdicts.append(await self.probe.lost())
        return verdicts


class TestStates:
    """The three states: fresh, failing under 5 min, failing over 5 min."""

    async def test_a_network_that_answers_is_not_lost(self) -> None:
        rig = _Rig()

        assert await rig.probe.lost() is False
        assert (rig.gauge("cache"), rig.gauge("dns")) == (1.0, 1.0)

    async def test_both_failing_under_five_minutes_is_not_lost(self) -> None:
        rig = _Rig()
        rig.down()

        assert not any(await rig.lost_after(299.0, step=23.0))
        assert (rig.gauge("cache"), rig.gauge("dns")) == (0.0, 0.0)

    async def test_both_failing_over_five_minutes_is_lost(self) -> None:
        rig = _Rig()
        rig.down()
        verdicts = await rig.lost_after(330.0)

        # Lost once the first failed probe is more than 300 s old
        assert verdicts == [False] * 11 + [True]

    async def test_one_answer_ends_the_failure(self) -> None:
        """A single answer starts the 5 min again: never lost here."""
        rig = _Rig()
        rig.down()
        first = await rig.lost_after(240.0)
        rig.down(cache=False, dns=False)
        rig.clock.now += 30.0
        assert await rig.probe.lost() is False
        rig.down()
        rig.clock.now += 30.0
        second = await rig.lost_after(270.0)

        assert not any(first + second)


class TestOneCheck:
    """A Redis restart or a DNS hiccup alone leaves the network found."""

    @pytest.mark.parametrize(
        ("cache", "dns"), [(True, False), (False, True)], ids=["cache", "dns"]
    )
    async def test_one_check_failing_alone_is_not_lost(
        self, cache: bool, dns: bool
    ) -> None:
        rig = _Rig()
        rig.down(cache=cache, dns=dns)

        assert not any(await rig.lost_after(900.0))
        assert rig.gauge("cache") == (0.0 if cache else 1.0)
        assert rig.gauge("dns") == (0.0 if dns else 1.0)


class TestProbing:
    async def test_one_probe_at_most_every_30_s(self) -> None:
        rig = _Rig()
        for offset in (0.0, 10.0, 29.9):
            rig.clock.now = 1000.0 + offset
            await rig.probe.lost()
        assert len(rig.dns.names) == 1
        assert rig.cache.ping.await_count == 1

        rig.clock.now = 1030.0
        await rig.probe.lost()
        assert len(rig.dns.names) == 2
        assert rig.cache.ping.await_count == 2

    async def test_the_cached_verdict_between_probes(self) -> None:
        """A request between probes gets the last probe's result."""
        rig = _Rig()
        await rig.probe.lost()
        rig.down()
        rig.clock.now += 10.0

        assert await rig.probe.lost() is False
        assert rig.gauge("dns") == 1.0

    async def test_concurrent_requests_share_one_probe(self) -> None:
        rig = _Rig()
        release = asyncio.Event()

        async def slow(name: str) -> object:
            rig.dns.names.append(name)
            await release.wait()
            return [("addr",)]

        probe = NetworkProbe(
            rig.cache, telemetry=rig.telemetry, resolve=slow, clock=rig.clock
        )
        waiting = asyncio.gather(probe.lost(), probe.lost(), probe.lost())
        await asyncio.sleep(0)
        release.set()

        assert await waiting == [False, False, False]
        assert len(rig.dns.names) == 1

    async def test_a_check_that_hangs_counts_as_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dropped packet: the check's bound ends it (the image's
        healthcheck gives /readyz 5 s)."""
        monkeypatch.setattr(network_probe, "_CHECK_TIMEOUT_S", 0.01)
        rig = _Rig()

        async def hang(name: str) -> object:
            await asyncio.Event().wait()
            return None

        async def ping() -> bool:
            await asyncio.Event().wait()
            return True

        rig.cache.ping.side_effect = ping
        probe = NetworkProbe(
            rig.cache, telemetry=rig.telemetry, resolve=hang, clock=rig.clock
        )

        assert await probe.lost() is False
        assert (rig.gauge("cache"), rig.gauge("dns")) == (0.0, 0.0)

    async def test_the_public_name_is_fixed(self) -> None:
        rig = _Rig()
        await rig.probe.lost()

        assert rig.dns.names == [network_probe._PUBLIC_NAME]

    def test_the_public_name_is_fully_qualified(self) -> None:
        """No search domain answers for it: a wildcard record there would
        answer while the public DNS is gone."""
        assert network_probe._PUBLIC_NAME.endswith(".")


class TestLogs:
    async def test_the_loss_and_its_end_are_logged_once(self) -> None:
        rig = _Rig()
        rig.down()
        with capture_logs() as logs:
            await rig.lost_after(400.0)
            rig.down(cache=False, dns=False)
            rig.clock.now += 30.0
            await rig.probe.lost()
            rig.clock.now += 30.0
            await rig.probe.lost()

        events = [e for e in logs if e["event"].startswith("network_")]
        assert [e["event"] for e in events] == ["network_lost", "network_recovered"]
        lost, recovered = events
        assert lost["log_level"] == "error"
        assert lost["dns_error"] == "gaierror"
        assert lost["seconds"] > 300
        assert recovered["log_level"] == "info"
        assert recovered["seconds"] == 420
        assert recovered["probes"] == 14

    async def test_a_failure_under_five_minutes_logs_nothing(self) -> None:
        rig = _Rig()
        rig.down()
        with capture_logs() as logs:
            await rig.lost_after(240.0)
            rig.down(cache=False, dns=False)
            rig.clock.now += 30.0
            await rig.probe.lost()

        assert not [e for e in logs if e["event"].startswith("network_")]
