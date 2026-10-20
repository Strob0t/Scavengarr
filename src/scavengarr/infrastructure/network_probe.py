"""Whether the app still reaches its network: the readiness probe's checks.

Production's container once lost its network namespace for 33 h (the VPN
container it shares was recreated without it) and reported healthy all the
while: ``/api/v1/healthz`` answers over loopback whatever fails outside.
``/api/v1/readyz`` asks this probe too. It pings the cache backend and
resolves a fixed public name, at most once every 30 s; a request in between
gets the last probe's verdict. Both failing for longer than 5 min is a lost
network. A single miss changes nothing, and neither does one check failing
alone (a Redis restart, a DNS hiccup of the VPN). Every probe sets the gauge
``scavengarr_network_ok`` per check (ledger D3, option A; row 125).
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable

import structlog

from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.telemetry import TelemetryPort

log = structlog.get_logger(__name__)

# A name that resolves wherever the public DNS answers: reserved for
# documentation (RFC 2606) and served by IANA. Fully qualified, so that no
# search domain of the resolver answers for it (the dev container's resolved
# an .invalid name through a search domain's wildcard record)
_PUBLIC_NAME = "example.com."
# One probe at most this often; the image's healthcheck asks every 30 s
_INTERVAL_S = 30.0
# Both checks failing this long: the network counts as lost
_LOST_AFTER_S = 300.0
# Each check's bound, so /readyz answers within the healthcheck's 5 s
_CHECK_TIMEOUT_S = 3.0


async def resolve_name(name: str) -> object:
    """Resolve *name* through the system resolver (``OSError`` if not)."""
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(name, 443, type=socket.SOCK_STREAM)


class NetworkProbe:
    """The cache backend's ping and a public name's resolution, with the
    verdict ``lost()``: both have failed for longer than 5 min.

    Args:
        cache: The cache backend (``CachePort.ping``).
        telemetry: Receives the gauge ``network_ok`` per check.
        resolve: Resolves a name, raising ``OSError`` when it cannot.
        clock: Monotonic seconds.
    """

    def __init__(
        self,
        cache: CachePort,
        *,
        telemetry: TelemetryPort,
        resolve: Callable[[str], Awaitable[object]] = resolve_name,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache = cache
        self._telemetry = telemetry
        self._resolve = resolve
        self._clock = clock
        # One probe at a time: requests that come meanwhile take its verdict
        self._lock = asyncio.Lock()
        self._probed_at: float | None = None
        # Both checks failing: the first such probe, the probes since, the
        # DNS error of the last one and whether the loss was logged
        self._failing_since: float | None = None
        self._failed = 0
        self._dns_error = ""
        self._lost = False

    async def lost(self) -> bool:
        """Whether both checks have failed for longer than 5 min; probes
        first when the last probe is 30 s old."""
        async with self._lock:
            now = self._clock()
            if self._probed_at is None or now - self._probed_at >= _INTERVAL_S:
                await self._probe(now)
            since = self._failing_since
            if since is None or now - since <= _LOST_AFTER_S:
                return False
            if not self._lost:
                self._lost = True
                log.error(
                    "network_lost",
                    seconds=round(now - since),
                    probes=self._failed,
                    dns_error=self._dns_error,
                )
            return True

    async def _probe(self, now: float) -> None:
        cache_ok, dns_error = await asyncio.gather(self._ping(), self._lookup())
        self._probed_at = now
        self._telemetry.record("network_ok", float(cache_ok), check="cache")
        self._telemetry.record("network_ok", float(dns_error is None), check="dns")
        if cache_ok or dns_error is None:
            if self._lost and self._failing_since is not None:
                log.info(
                    "network_recovered",
                    seconds=round(now - self._failing_since),
                    probes=self._failed,
                )
            self._failing_since = None
            self._failed = 0
            self._lost = False
            return
        if self._failing_since is None:
            self._failing_since = now
        self._failed += 1
        self._dns_error = dns_error

    async def _ping(self) -> bool:
        """The cache backend's ping; one that hangs counts as no answer."""
        try:
            async with asyncio.timeout(_CHECK_TIMEOUT_S):
                return await self._cache.ping()
        except TimeoutError:
            return False

    async def _lookup(self) -> str | None:
        """The public name's resolution: ``None`` when it resolved, else
        the error's name."""
        try:
            async with asyncio.timeout(_CHECK_TIMEOUT_S):
                await self._resolve(_PUBLIC_NAME)
        except (OSError, TimeoutError) as exc:
            return type(exc).__name__
        return None
