"""Keep requests to scraped URLs out of the local network (blind SSRF).

Scraped pages decide which URLs Scavengarr requests: download links,
hoster embeds, CDN URLs and every redirect they send. A hostile page could
point them at the LAN (``http://192.168.1.1/...``), at cloud metadata
(``169.254.169.254``) or at Scavengarr itself. The shared HTTP client runs
:class:`PrivateAddressGuard` as a ``request`` event hook, so every request
and every redirect hop to a target that is not globally routable is
refused. Hostnames are resolved (cached) so names that resolve into the LAN
are refused too. Connections go to the addresses the guard checked
(:class:`GuardedNetworkBackend`): a second lookup at connect time could
answer with a LAN address (DNS rebinding).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Iterable

import httpcore
import httpx
import structlog

# httpcore.AnyIOBackend is typed by its fallback for a missing anyio
from httpcore._backends.anyio import AnyIOBackend

log = structlog.get_logger(__name__)

# How long a lookup answers checks and connections (a CDN's addresses may
# change; the gluetun resolver caches as well)
_DNS_CACHE_TTL_S = 60.0
_DNS_CACHE_MAX = 4096

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class PrivateAddressError(httpx.TransportError):
    """The request target is not a globally routable address."""


def _is_global_ip(ip: _IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


class PrivateAddressGuard:
    """httpx ``request`` event hook refusing non-public targets.

    It also tells :class:`GuardedNetworkBackend` where to connect, from the
    same cached lookup. *allowed_hosts* are internal services Scavengarr
    talks to on purpose (the Byparr/FlareSolverr sidecar).
    """

    def __init__(self, allowed_hosts: frozenset[str] = frozenset()) -> None:
        self._allowed_hosts = allowed_hosts
        self._dns_cache: dict[str, tuple[tuple[_IPAddress, ...], float]] = {}

    async def __call__(self, request: httpx.Request) -> None:
        host = request.url.host
        try:
            await self.addresses(host)
        except PrivateAddressError:
            log.warning("private_address_refused", host=host, url=str(request.url))
            raise PrivateAddressError(
                f"refused request to a non-public address: {host}", request=request
            ) from None
        except OSError:
            return  # unresolvable: the connection fails on its own

    async def addresses(self, host: str) -> tuple[str, ...]:
        """Where to connect for *host*: its addresses, IPv4 first.

        An allowed host comes back as it is (the system resolves it). Raises
        :class:`PrivateAddressError` when an address is not public and
        ``OSError`` when the name does not resolve.
        """
        if host in self._allowed_hosts:
            return (host,)
        try:
            ips: tuple[_IPAddress, ...] = (ipaddress.ip_address(host),)
        except ValueError:
            ips = await self._resolve(host)
        if not all(_is_global_ip(ip) for ip in ips):
            raise PrivateAddressError(f"refused a non-public address: {host}")
        return tuple(str(ip) for ip in sorted(ips, key=lambda ip: ip.version))

    async def _resolve(self, host: str) -> tuple[_IPAddress, ...]:
        now = time.monotonic()
        cached = self._dns_cache.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
        ips = tuple(dict.fromkeys(ipaddress.ip_address(info[4][0]) for info in infos))
        if len(self._dns_cache) >= _DNS_CACHE_MAX:
            self._dns_cache.clear()
        self._dns_cache[host] = (ips, now + _DNS_CACHE_TTL_S)
        return ips


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend connecting to the addresses the guard checked.

    The guard and httpcore used to resolve a name separately, so a DNS
    server could answer the guard with a public address and the connection
    with a LAN one. TLS still checks the hostname: httpcore sends the
    request's host as SNI, whatever address the socket went to. The
    addresses are tried in turn, IPv4 first.
    """

    def __init__(
        self,
        guard: PrivateAddressGuard,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self.guard = guard
        self._backend = backend or AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await self.guard.addresses(host)
        except PrivateAddressError:
            log.warning("private_address_refused", host=host)
            raise
        except OSError as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from exc
        failure: Exception | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc
        assert failure is not None  # addresses() never returns an empty tuple
        raise failure

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """httpx's transport whose connections go through :class:`GuardedNetworkBackend`."""

    def __init__(
        self, guard: PrivateAddressGuard, *, limits: httpx.Limits, http2: bool
    ) -> None:
        super().__init__(limits=limits, http2=http2)
        # httpx takes no network backend: its pool is rebuilt with one
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http2=http2,
            network_backend=GuardedNetworkBackend(guard),
        )
