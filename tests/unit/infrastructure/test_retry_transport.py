"""Tests for RetryTransport (rate limiting + 429/503 retry)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from scavengarr.infrastructure.common.rate_limiter import DomainRateLimiter
from scavengarr.infrastructure.common.retry_transport import RetryTransport


def _make_response(
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build an httpx.Response for transport-level tests."""
    return httpx.Response(
        status_code=status,
        headers=headers or {},
    )


def _make_request(url: str = "https://example.com/page") -> httpx.Request:
    return httpx.Request("GET", url)


def _make_transport(
    responses: list[httpx.Response] | httpx.Response,
    *,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    max_backoff: float = 30.0,
    rps: float = 0.0,
) -> RetryTransport:
    """Create a RetryTransport with a mock inner transport."""
    mock_wrapped = AsyncMock(spec=httpx.AsyncBaseTransport)
    if isinstance(responses, list):
        mock_wrapped.handle_async_request = AsyncMock(
            side_effect=responses,
        )
    else:
        mock_wrapped.handle_async_request = AsyncMock(
            return_value=responses,
        )
    limiter = DomainRateLimiter(default_rps=rps, burst=10)
    return RetryTransport(
        wrapped=mock_wrapped,
        rate_limiter=limiter,
        max_retries=max_retries,
        backoff_base=backoff_base,
        max_backoff=max_backoff,
    )


class TestRetryTransport:
    @pytest.mark.asyncio()
    async def test_passes_through_successful_response(self) -> None:
        transport = _make_transport(_make_response(200))
        resp = await transport.handle_async_request(_make_request())
        assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_retries_on_429_then_succeeds(self) -> None:
        responses = [
            _make_response(429),
            _make_response(200),
        ]
        transport = _make_transport(responses)
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 200
        assert m.sleep.await_count == 1
        assert transport._wrapped.handle_async_request.call_count == 2

    @pytest.mark.asyncio()
    async def test_retries_on_503_then_succeeds(self) -> None:
        responses = [
            _make_response(503),
            _make_response(200),
        ]
        transport = _make_transport(responses)
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 200
        assert m.sleep.await_count == 1

    @pytest.mark.asyncio()
    async def test_respects_retry_after_header(self) -> None:
        responses = [
            _make_response(429, headers={"Retry-After": "5"}),
            _make_response(200),
        ]
        transport = _make_transport(responses)
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            await transport.handle_async_request(_make_request())

        m.sleep.assert_awaited_once_with(5.0)

    @pytest.mark.asyncio()
    async def test_caps_retry_after_at_max_backoff(self) -> None:
        responses = [
            _make_response(429, headers={"Retry-After": "120"}),
            _make_response(200),
        ]
        transport = _make_transport(responses, max_backoff=10.0)
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            await transport.handle_async_request(_make_request())

        m.sleep.assert_awaited_once_with(10.0)

    @pytest.mark.asyncio()
    async def test_gives_up_after_max_retries(self) -> None:
        transport = _make_transport(
            _make_response(429),
            max_retries=2,
        )
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 429
        # 2 retries + 1 initial = 3 total attempts, 2 sleeps
        assert m.sleep.await_count == 2
        assert transport._wrapped.handle_async_request.call_count == 3

    @pytest.mark.asyncio()
    async def test_exponential_backoff_without_retry_after(self) -> None:
        """Backoff grows exponentially: base*2^0, base*2^1, ..."""
        responses = [
            _make_response(429),
            _make_response(429),
            _make_response(200),
        ]
        transport = _make_transport(
            responses,
            backoff_base=1.0,
            max_backoff=100.0,
        )
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            with patch(
                "scavengarr.infrastructure.common.retry_transport.random"
            ) as rng:
                rng.uniform.return_value = 0.0  # zero jitter for predictable test
                await transport.handle_async_request(_make_request())

        delays = [call.args[0] for call in m.sleep.await_args_list]
        # attempt 0: 1.0 * 2^0 = 1.0, attempt 1: 1.0 * 2^1 = 2.0
        assert delays == [1.0, 2.0]

    @pytest.mark.asyncio()
    async def test_does_not_retry_non_retryable_status(self) -> None:
        for status in (400, 404, 500):
            transport = _make_transport(_make_response(status))
            resp = await transport.handle_async_request(_make_request())
            assert resp.status_code == status
            assert transport._wrapped.handle_async_request.call_count == 1

    @pytest.mark.asyncio()
    async def test_rate_limiter_called_per_attempt(self) -> None:
        responses = [
            _make_response(429),
            _make_response(200),
        ]
        transport = _make_transport(responses)
        # Spy on the rate limiter
        transport._rate_limiter.acquire = AsyncMock()

        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            await transport.handle_async_request(_make_request())

        assert transport._rate_limiter.acquire.await_count == 2

    @pytest.mark.asyncio()
    async def test_aclose_delegates_to_wrapped(self) -> None:
        transport = _make_transport(_make_response(200))
        transport._wrapped.aclose = AsyncMock()
        await transport.aclose()
        transport._wrapped.aclose.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_zero_retries_disables_retry(self) -> None:
        transport = _make_transport(
            _make_response(429),
            max_retries=0,
        )
        resp = await transport.handle_async_request(_make_request())
        assert resp.status_code == 429
        assert transport._wrapped.handle_async_request.call_count == 1


class TestCachedRetryableResponse:
    """A 429/503 served from Cloudflare's cache won't change on retry."""

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("cache_status", ["HIT", "STALE", "UPDATING"])
    async def test_cached_429_returned_without_retry(self, cache_status: str) -> None:
        transport = _make_transport(
            [_make_response(429, {"cf-cache-status": cache_status})]
        )
        with (
            patch.object(transport._rate_limiter, "record_throttle") as throttle,
            patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m,
        ):
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 429
        assert transport._wrapped.handle_async_request.call_count == 1
        m.sleep.assert_not_awaited()
        throttle.assert_not_called()  # the origin did not throttle us now

    @pytest.mark.asyncio()
    async def test_uncached_429_still_retried(self) -> None:
        transport = _make_transport(
            [
                _make_response(429, {"cf-cache-status": "MISS"}),
                _make_response(200),
            ]
        )
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 200
        assert transport._wrapped.handle_async_request.call_count == 2


_CHALLENGE_HTML = "<html><title>Just a moment...</title>challenge-platform</html>"


def _respx_transport(router: respx.MockRouter, max_retries: int = 3) -> RetryTransport:
    """A RetryTransport whose inner transport answers from *router*."""
    return RetryTransport(
        wrapped=httpx.MockTransport(router.async_handler),
        rate_limiter=DomainRateLimiter(default_rps=0.0, burst=10),
        max_retries=max_retries,
        backoff_base=1.0,
        max_backoff=30.0,
    )


class TestChallengeAnswer:
    """A 403 or 503 that is a Cloudflare challenge is the edge talking, not
    the origin: retrying it gets the same page, and halving the domain's
    rate for it starves a site that was never overloaded."""

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("status", [403, 503])
    async def test_a_challenge_header_is_returned_at_once(self, status: int) -> None:
        router = respx.MockRouter(assert_all_called=False)
        route = router.get("https://example.com/page").respond(
            status, headers={"cf-mitigated": "challenge"}, text=_CHALLENGE_HTML
        )
        transport = _respx_transport(router)
        with (
            patch.object(transport._rate_limiter, "record_throttle") as throttle,
            patch.object(transport._rate_limiter, "record_success") as success,
            patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m,
        ):
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == status
        assert route.call_count == 1
        m.sleep.assert_not_awaited()
        throttle.assert_not_called()
        success.assert_not_called()

    @pytest.mark.asyncio()
    async def test_a_challenge_page_without_the_header_is_returned_at_once(
        self,
    ) -> None:
        router = respx.MockRouter(assert_all_called=False)
        route = router.get("https://example.com/page").respond(
            503, html=_CHALLENGE_HTML
        )
        transport = _respx_transport(router)
        with (
            patch.object(transport._rate_limiter, "record_throttle") as throttle,
            patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m,
        ):
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 503
        assert "Just a moment" in resp.text  # the body stays readable
        assert route.call_count == 1
        throttle.assert_not_called()

    @pytest.mark.asyncio()
    async def test_a_plain_503_is_still_retried(self) -> None:
        router = respx.MockRouter(assert_all_called=False)
        route = router.get("https://example.com/page").mock(
            side_effect=[
                httpx.Response(503, html="<html>Service Unavailable</html>"),
                httpx.Response(200, text="ok"),
            ]
        )
        transport = _respx_transport(router)
        with (
            patch.object(transport._rate_limiter, "record_throttle") as throttle,
            patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m,
        ):
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 200
        assert route.call_count == 2
        throttle.assert_called_once()

    @pytest.mark.asyncio()
    async def test_a_429_waits_for_retry_after(self) -> None:
        router = respx.MockRouter(assert_all_called=False)
        route = router.get("https://example.com/page").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(200, text="ok"),
            ]
        )
        transport = _respx_transport(router)
        with patch("scavengarr.infrastructure.common.retry_transport.asyncio") as m:
            m.sleep = AsyncMock()
            resp = await transport.handle_async_request(_make_request())

        assert resp.status_code == 200
        assert route.call_count == 2
        m.sleep.assert_awaited_once_with(2.0)
