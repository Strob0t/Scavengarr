"""Per-domain token-bucket rate limiter for outgoing HTTP requests."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)


class TokenBucket:
    """Simple token-bucket rate limiter.

    Args:
        rate: Tokens replenished per second.
        burst: Maximum bucket size (allows short bursts).
    """

    def __init__(self, rate: float, burst: int = 10) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        if self._rate <= 0:
            return  # unlimited

        async with self._lock:
            self._refill()
            while self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now


class DomainRateLimiter:
    """Manages per-domain token-bucket rate limiters.

    Args:
        default_rps: Default requests-per-second per domain. 0 = unlimited.
        burst: Maximum burst size per domain.
    """

    def __init__(self, default_rps: float = 5.0, burst: int = 10) -> None:
        self._default_rps = default_rps
        self._burst = burst
        self._buckets: dict[str, TokenBucket] = {}

    def _get_domain(self, url: str) -> str:
        """Extract second-level domain from URL."""
        try:
            hostname = urlparse(url).hostname
            if not hostname:
                return ""
            parts = hostname.split(".")
            return parts[-2] if len(parts) >= 2 else parts[0]
        except Exception:  # noqa: BLE001
            return ""

    def _get_bucket(self, domain: str) -> TokenBucket:
        """Get or create a bucket for the given domain."""
        if domain not in self._buckets:
            self._buckets[domain] = TokenBucket(
                rate=self._default_rps, burst=self._burst
            )
        return self._buckets[domain]

    async def acquire(self, url: str) -> None:
        """Wait for rate limit clearance for the given URL's domain."""
        if self._default_rps <= 0:
            return

        domain = self._get_domain(url)
        if not domain:
            return

        bucket = self._get_bucket(domain)
        await bucket.acquire()
