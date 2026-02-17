"""httpx transport with per-domain rate limiting and 429/503 retry."""

from __future__ import annotations

import asyncio
import random

import httpx
import structlog

from scavengarr.infrastructure.common.rate_limiter import DomainRateLimiter

log = structlog.get_logger(__name__)

_DEFAULT_RETRYABLE = frozenset({429, 503})


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    """Parse ``Retry-After`` header value (seconds only).

    Returns the delay in seconds, or ``None`` if the header is missing
    or unparseable.  HTTP-date format is intentionally ignored — most
    rate-limiting servers use the integer-seconds form.
    """
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


class RetryTransport(httpx.AsyncBaseTransport):
    """Wraps an httpx transport with rate limiting and retry on 429/503.

    **Proactive:** calls ``DomainRateLimiter.acquire()`` before every
    request to throttle per-domain request rate.

    **Reactive:** on retryable HTTP status codes (429, 503 by default),
    waits using exponential backoff (with jitter) and retries up to
    *max_retries* times.  Respects ``Retry-After`` header when present.
    """

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        rate_limiter: DomainRateLimiter,
        *,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        max_backoff: float = 30.0,
        retryable_status_codes: frozenset[int] = _DEFAULT_RETRYABLE,
    ) -> None:
        self._wrapped = wrapped
        self._rate_limiter = rate_limiter
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_backoff = max_backoff
        self._retryable = retryable_status_codes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send *request* through the wrapped transport with rate limiting.

        On retryable status codes, retries with exponential backoff.
        """
        last_response: httpx.Response | None = None

        for attempt in range(1 + self._max_retries):
            # Proactive: per-domain rate limiting
            await self._rate_limiter.acquire(str(request.url))

            response = await self._wrapped.handle_async_request(request)

            if response.status_code not in self._retryable:
                return response

            # Last attempt — return whatever we got
            if attempt == self._max_retries:
                return response

            # Read + close the retryable response before retrying
            last_response = response
            await last_response.aread()
            await last_response.aclose()

            delay = self._compute_delay(response, attempt)
            log.info(
                "http_retry",
                url=str(request.url),
                status=response.status_code,
                attempt=attempt + 1,
                delay=round(delay, 2),
            )
            await asyncio.sleep(delay)

        # Unreachable, but satisfies type checker
        assert last_response is not None
        return last_response  # pragma: no cover

    def _compute_delay(self, response: httpx.Response, attempt: int) -> float:
        """Compute retry delay from Retry-After or exponential backoff."""
        retry_after = _parse_retry_after(response.headers)
        if retry_after is not None:
            return min(retry_after, self._max_backoff)

        # Exponential backoff with jitter
        delay = self._backoff_base * (2**attempt)
        jitter = random.uniform(0, self._backoff_base)  # noqa: S311
        return min(delay + jitter, self._max_backoff)

    async def aclose(self) -> None:
        """Close the wrapped transport."""
        await self._wrapped.aclose()
