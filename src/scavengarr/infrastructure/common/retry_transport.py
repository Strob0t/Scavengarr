"""httpx transport with per-domain rate limiting and 429/503 retry.

A 403 or 503 that is a Cloudflare challenge (``cf-mitigated: challenge``,
or a challenge page) is the edge talking, not the origin: it is returned
at once, neither retried nor fed back to the rate limiter.
"""

from __future__ import annotations

import asyncio
import random

import httpx
import structlog

from scavengarr.infrastructure.captcha.detect import detect_challenge
from scavengarr.infrastructure.common.rate_limiter import DomainRateLimiter

log = structlog.get_logger(__name__)

_DEFAULT_RETRYABLE = frozenset({429, 503})
# cf-cache-status values of responses served from Cloudflare's cache
_CACHED_STATUSES = frozenset({"HIT", "STALE", "UPDATING"})
# Statuses a Cloudflare challenge page comes with
_CHALLENGE_STATUSES = frozenset({403, 503})


async def _is_challenge(response: httpx.Response) -> bool:
    """Whether *response* is a challenge: Cloudflare's header, or an HTML
    body ``detect_challenge`` recognises (read here; it stays readable)."""
    if response.status_code not in _CHALLENGE_STATUSES:
        return False
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if "text/html" not in response.headers.get("content-type", "").lower():
        return False
    await response.aread()
    return (
        detect_challenge(response.status_code, response.text, response.headers)
        is not None
    )


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
        Feeds back success/throttle signals to the adaptive rate limiter.
        """
        url_str = str(request.url)
        last_response: httpx.Response | None = None

        for attempt in range(1 + self._max_retries):
            # Proactive: per-domain rate limiting
            await self._rate_limiter.acquire(url_str)

            response = await self._wrapped.handle_async_request(request)

            # The edge's challenge, not the origin's answer: a retry gets
            # the same page, and the origin's rate is not the issue
            if await _is_challenge(response):
                log.debug("http_challenge", url=url_str, status=response.status_code)
                return response

            if response.status_code not in self._retryable:
                # Adaptive feedback: successful response
                self._rate_limiter.record_success(url_str)
                return response

            # A cached 429/503 (Cloudflare can cache them for hours) comes back
            # unchanged on retry and says nothing about the origin's load now
            if response.headers.get("cf-cache-status") in _CACHED_STATUSES:
                log.debug("http_cached_error", url=url_str, status=response.status_code)
                return response

            # Adaptive feedback: throttled (429/503)
            self._rate_limiter.record_throttle(url_str)

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
                url=url_str,
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
