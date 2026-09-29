"""Keep requests to scraped URLs out of the local network (blind SSRF).

Scraped pages decide which URLs Scavengarr requests: download links,
hoster embeds, CDN URLs and every redirect they send. A hostile page could
point them at the LAN (``http://192.168.1.1/...``), at cloud metadata
(``169.254.169.254``) or at Scavengarr itself. The shared HTTP client runs
:class:`PrivateAddressGuard` as a ``request`` event hook, so every request
and every redirect hop to a target that is not globally routable is
refused. Hostnames are resolved (cached) so names that resolve into the LAN
are refused too; the check and httpx's own lookup are separate, so a DNS
rebinding attack is out of scope.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time

import httpx
import structlog

log = structlog.get_logger(__name__)

_DNS_CACHE_TTL_S = 300.0
_DNS_CACHE_MAX = 4096


class PrivateAddressError(httpx.TransportError):
    """The request target is not a globally routable address."""


def _is_global_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global


class PrivateAddressGuard:
    """httpx ``request`` event hook refusing non-public targets.

    *allowed_hosts* are internal services Scavengarr talks to on purpose
    (the Byparr/FlareSolverr sidecar).
    """

    def __init__(self, allowed_hosts: frozenset[str] = frozenset()) -> None:
        self._allowed_hosts = allowed_hosts
        self._dns_cache: dict[str, tuple[bool, float]] = {}

    async def __call__(self, request: httpx.Request) -> None:
        host = request.url.host
        if host in self._allowed_hosts or await self._is_public(host):
            return
        log.warning("private_address_refused", host=host, url=str(request.url))
        raise PrivateAddressError(
            f"refused request to a non-public address: {host}", request=request
        )

    async def _is_public(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return await self._resolves_publicly(host)
        return _is_global_ip(ip)

    async def _resolves_publicly(self, host: str) -> bool:
        now = time.monotonic()
        cached = self._dns_cache.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, type=socket.SOCK_STREAM
            )
        except OSError:
            # Unresolvable: the request fails on its own
            return True
        public = all(_is_global_ip(ipaddress.ip_address(info[4][0])) for info in infos)
        if len(self._dns_cache) >= _DNS_CACHE_MAX:
            self._dns_cache.clear()
        self._dns_cache[host] = (public, now + _DNS_CACHE_TTL_S)
        return public
