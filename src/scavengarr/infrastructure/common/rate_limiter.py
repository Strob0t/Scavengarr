"""Per-domain token-bucket rate limiter for outgoing HTTP requests.

Supports adaptive AIMD (Additive Increase / Multiplicative Decrease) rate
adjustment based on target-server feedback (429/503 → halve, success → grow).
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)


class TokenBucket:
    """Token-bucket rate limiter with optional AIMD adaptive rate.

    When *adaptive* is ``True``, the rate adjusts automatically based on
    feedback from :meth:`record_success`, :meth:`record_throttle`, and
    :meth:`record_timeout`.  The algorithm mirrors TCP congestion control:

    - **Success**: rate +10% (additive increase), capped at *max_rate*.
    - **429/503**: rate halved (multiplicative decrease), floored at
      *min_rate*.
    - **Timeout**: rate −25%, floored at *min_rate*.

    Args:
        rate: Tokens replenished per second (initial rate).
        burst: Maximum bucket size (allows short bursts).
        adaptive: Enable AIMD rate adaptation.
        min_rate: Lower bound for adaptive rate (rps).
        max_rate: Upper bound for adaptive rate (rps).
    """

    def __init__(
        self,
        rate: float,
        burst: int = 10,
        *,
        adaptive: bool = False,
        min_rate: float = 0.5,
        max_rate: float = 50.0,
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._adaptive = adaptive
        self._min_rate = min_rate
        self._max_rate = max_rate

    @property
    def rate(self) -> float:
        """Current tokens-per-second rate."""
        return self._rate

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

    # -- AIMD feedback methods ------------------------------------------

    def record_success(self) -> None:
        """Record a successful response — slowly increase rate (+10%)."""
        if not self._adaptive:
            return
        self._rate = min(self._max_rate, self._rate * 1.1)

    def record_throttle(self) -> None:
        """Record a 429/503 — immediately halve rate (×0.5)."""
        if not self._adaptive:
            return
        old = self._rate
        self._rate = max(self._min_rate, self._rate * 0.5)
        log.debug(
            "rate_limit_throttle", old_rps=round(old, 2), new_rps=round(self._rate, 2)
        )

    def record_timeout(self) -> None:
        """Record a timeout — reduce rate by 25% (×0.75)."""
        if not self._adaptive:
            return
        old = self._rate
        self._rate = max(self._min_rate, self._rate * 0.75)
        log.debug(
            "rate_limit_timeout", old_rps=round(old, 2), new_rps=round(self._rate, 2)
        )


class DomainRateLimiter:
    """Manages per-domain token-bucket rate limiters.

    Args:
        default_rps: Default requests-per-second per domain. 0 = unlimited.
        burst: Maximum burst size per domain.
        adaptive: Enable AIMD rate adaptation per domain.
        min_rate: Lower bound for adaptive rate (rps).
        max_rate: Upper bound for adaptive rate (rps).
    """

    def __init__(
        self,
        default_rps: float = 5.0,
        burst: int = 10,
        *,
        adaptive: bool = False,
        min_rate: float = 0.5,
        max_rate: float = 50.0,
    ) -> None:
        self._default_rps = default_rps
        self._burst = burst
        self._adaptive = adaptive
        self._min_rate = min_rate
        self._max_rate = max_rate
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
                rate=self._default_rps,
                burst=self._burst,
                adaptive=self._adaptive,
                min_rate=self._min_rate,
                max_rate=self._max_rate,
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

    def record_success(self, url: str) -> None:
        """Record a successful response for the URL's domain."""
        domain = self._get_domain(url)
        if domain and domain in self._buckets:
            self._buckets[domain].record_success()

    def record_throttle(self, url: str) -> None:
        """Record a 429/503 for the URL's domain."""
        domain = self._get_domain(url)
        if domain and domain in self._buckets:
            self._buckets[domain].record_throttle()

    def record_timeout(self, url: str) -> None:
        """Record a timeout for the URL's domain."""
        domain = self._get_domain(url)
        if domain and domain in self._buckets:
            self._buckets[domain].record_timeout()
