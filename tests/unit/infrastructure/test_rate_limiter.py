"""Tests for DomainRateLimiter and TokenBucket."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.common.rate_limiter import (
    DomainRateLimiter,
    TokenBucket,
)


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self) -> None:
        bucket = TokenBucket(rate=10.0, burst=5)
        await bucket.acquire()
        # No error — token consumed

    @pytest.mark.asyncio
    async def test_unlimited_rate_skips(self) -> None:
        bucket = TokenBucket(rate=0.0, burst=5)
        # Should return immediately without consuming anything
        await bucket.acquire()
        await bucket.acquire()

    @pytest.mark.asyncio
    async def test_burst_allows_multiple_immediate(self) -> None:
        bucket = TokenBucket(rate=1.0, burst=3)
        # Should allow 3 immediate acquires (burst size)
        for _ in range(3):
            await bucket.acquire()


class TestDomainRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_for_url(self) -> None:
        limiter = DomainRateLimiter(default_rps=10.0, burst=5)
        await limiter.acquire("https://example.com/page")
        # No error — bucket created and token consumed

    @pytest.mark.asyncio
    async def test_unlimited_rps_skips(self) -> None:
        limiter = DomainRateLimiter(default_rps=0.0)
        await limiter.acquire("https://example.com/page")
        # Should return immediately

    @pytest.mark.asyncio
    async def test_empty_domain_skips(self) -> None:
        limiter = DomainRateLimiter(default_rps=5.0)
        await limiter.acquire("not-a-url")
        # Should return without error

    @pytest.mark.asyncio
    async def test_different_domains_get_separate_buckets(self) -> None:
        limiter = DomainRateLimiter(default_rps=10.0, burst=2)
        await limiter.acquire("https://example.com/a")
        await limiter.acquire("https://other.com/b")
        # Both should work — separate buckets
        assert len(limiter._buckets) == 2

    @pytest.mark.asyncio
    async def test_same_domain_shares_bucket(self) -> None:
        limiter = DomainRateLimiter(default_rps=10.0, burst=5)
        await limiter.acquire("https://example.com/a")
        await limiter.acquire("https://example.com/b")
        assert len(limiter._buckets) == 1
