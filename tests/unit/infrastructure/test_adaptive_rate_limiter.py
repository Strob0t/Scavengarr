"""Tests for adaptive (multiplicative) rate limiting."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.common.rate_limiter import DomainRateLimiter, TokenBucket


class TestTokenBucketAdaptive:
    """Adaptive rate behavior on TokenBucket."""

    def test_record_success_increases_rate(self) -> None:
        bucket = TokenBucket(rate=10.0, adaptive=True, min_rate=0.5, max_rate=50.0)
        bucket.record_success()
        assert bucket.rate == pytest.approx(11.0)  # 10 * 1.1

    def test_record_throttle_halves_rate(self) -> None:
        bucket = TokenBucket(rate=10.0, adaptive=True, min_rate=0.5, max_rate=50.0)
        bucket.record_throttle()
        assert bucket.rate == pytest.approx(5.0)  # 10 * 0.5

    def test_rate_does_not_exceed_max(self) -> None:
        bucket = TokenBucket(rate=48.0, adaptive=True, min_rate=0.5, max_rate=50.0)
        bucket.record_success()  # 48 * 1.1 = 52.8 → capped at 50
        assert bucket.rate == pytest.approx(50.0)

    def test_rate_does_not_go_below_min(self) -> None:
        bucket = TokenBucket(rate=0.8, adaptive=True, min_rate=0.5, max_rate=50.0)
        bucket.record_throttle()  # 0.8 * 0.5 = 0.4 → floored at 0.5
        assert bucket.rate == pytest.approx(0.5)

    def test_non_adaptive_ignores_feedback(self) -> None:
        bucket = TokenBucket(rate=10.0, adaptive=False)
        bucket.record_success()
        bucket.record_throttle()
        assert bucket.rate == 10.0  # unchanged

    def test_multiple_successes_compound(self) -> None:
        bucket = TokenBucket(rate=10.0, adaptive=True, min_rate=0.5, max_rate=50.0)
        for _ in range(5):
            bucket.record_success()
        expected = 10.0 * (1.1**5)
        assert bucket.rate == pytest.approx(expected)

    def test_recovery_after_throttle(self) -> None:
        """After throttle (halve), success slowly recovers."""
        bucket = TokenBucket(rate=10.0, adaptive=True, min_rate=0.5, max_rate=50.0)
        bucket.record_throttle()  # 10 → 5
        assert bucket.rate == pytest.approx(5.0)
        bucket.record_success()  # 5 → 5.5
        assert bucket.rate == pytest.approx(5.5)
        bucket.record_success()  # 5.5 → 6.05
        assert bucket.rate == pytest.approx(6.05)


class TestDomainRateLimiterAdaptive:
    """Per-domain adaptive feedback via DomainRateLimiter."""

    @pytest.mark.asyncio()
    async def test_record_success_on_known_domain(self) -> None:
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=True, max_rate=50.0)
        await limiter.acquire("https://example.com/page")
        limiter.record_success("https://example.com/other")
        bucket = limiter._buckets.get("example")
        assert bucket is not None
        assert bucket.rate == pytest.approx(11.0)

    @pytest.mark.asyncio()
    async def test_record_throttle_on_known_domain(self) -> None:
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=True, max_rate=50.0)
        await limiter.acquire("https://voe.sx/e/abc")
        limiter.record_throttle("https://voe.sx/e/def")
        bucket = limiter._buckets.get("voe")
        assert bucket is not None
        assert bucket.rate == pytest.approx(5.0)

    def test_record_on_unknown_domain_is_noop(self) -> None:
        """Feedback for a domain that hasn't been acquired yet is ignored."""
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=True, max_rate=50.0)
        limiter.record_success("https://unknown.com/page")
        assert "unknown" not in limiter._buckets

    @pytest.mark.asyncio()
    async def test_domains_are_independent(self) -> None:
        """Throttle on domain A doesn't affect domain B."""
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=True, max_rate=50.0)
        await limiter.acquire("https://alpha.com/1")
        await limiter.acquire("https://beta.com/1")

        limiter.record_throttle("https://alpha.com/2")

        alpha = limiter._buckets["alpha"]
        beta = limiter._buckets["beta"]
        assert alpha.rate == pytest.approx(5.0)  # halved
        assert beta.rate == pytest.approx(10.0)  # unchanged

    @pytest.mark.asyncio()
    async def test_non_adaptive_limiter_ignores_feedback(self) -> None:
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=False)
        await limiter.acquire("https://example.com/page")
        limiter.record_throttle("https://example.com/page")
        bucket = limiter._buckets["example"]
        assert bucket.rate == 10.0  # unchanged

    @pytest.mark.asyncio()
    async def test_hosts_share_a_bucket_by_second_level_label(self) -> None:
        """``www.example.com`` and ``example.org`` share one bucket: a 429
        for one URL slows the other (the label before the top-level domain
        names the bucket)."""
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=True, max_rate=50.0)
        await limiter.acquire("https://www.example.com/a")
        await limiter.acquire("https://example.org/b")
        assert len(limiter._buckets) == 1

        limiter.record_throttle("https://www.example.com/a")

        assert limiter._buckets["example"].rate == pytest.approx(5.0)
        await limiter.acquire("https://example.org/b")
        limiter.record_success("https://example.org/b")
        assert limiter._buckets["example"].rate == pytest.approx(5.5)

    @pytest.mark.asyncio()
    async def test_subdomain_does_not_name_the_bucket(self) -> None:
        """Two sites that differ only in their subdomain are one bucket, and
        sites that differ in the second-level label are two."""
        limiter = DomainRateLimiter(default_rps=10.0, adaptive=True, max_rate=50.0)
        await limiter.acquire("https://a.example.com/")
        await limiter.acquire("https://b.example.com/")
        await limiter.acquire("https://www.other.com/")
        assert sorted(limiter._buckets) == ["example", "other"]
