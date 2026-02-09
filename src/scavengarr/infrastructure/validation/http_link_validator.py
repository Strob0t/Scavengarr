"""HTTP-based link validator using HEAD requests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from httpx import AsyncClient, HTTPError, TimeoutException

if TYPE_CHECKING:
    from httpx import AsyncClient

log = structlog.get_logger(__name__)


class HttpLinkValidator:
    """Validates download links via HTTP HEAD requests.

    - Sends HEAD request (no body download) to check availability.
    - Considers 2xx/3xx as valid, 4xx/5xx/timeout as invalid.
    - Uses semaphore to limit concurrent requests (avoid rate-limits).

    Args:
        http_client: Shared httpx.AsyncClient (injected).
        timeout_seconds: Max time per validation (default: 5s).
        max_concurrent: Max parallel validations (default: 20).
    """

    def __init__(
        self,
        http_client: AsyncClient,
        timeout_seconds: float = 5.0,
        max_concurrent: int = 20,
    ) -> None:
        self.http_client = http_client
        self.timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def validate(self, url: str) -> bool:
        """Validate single URL.

        Returns:
            True if reachable (2xx/3xx), False otherwise.
        """
        async with self._semaphore:
            try:
                # HEAD request (no body, fast)
                response = await self.http_client.head(
                    url,
                    timeout=self.timeout,
                    follow_redirects=True,  # Follow redirects to final destination
                )
                is_valid = response.status_code < 400

                log.debug(
                    "link_validated",
                    url=url,
                    status_code=response.status_code,
                    valid=is_valid,
                )
                return is_valid

            except TimeoutException:
                log.warning("link_validation_timeout", url=url, timeout=self.timeout)
                return False

            except HTTPError as e:
                log.warning("link_validation_error", url=url, error=str(e))
                return False

            except Exception as e:
                # Catch-all for DNS errors, connection refused, etc.
                log.error("link_validation_unexpected_error", url=url, error=str(e))
                return False

    async def validate_batch(self, urls: list[str]) -> dict[str, bool]:
        """Validate multiple URLs concurrently.

        Args:
            urls: List of download links.

        Returns:
            Dict mapping url -> is_valid.
        """
        if not urls:
            return {}

        log.info("batch_validation_started", count=len(urls))

        # Parallel validation (semaphore limits concurrency)
        tasks = [self.validate(url) for url in urls]
        results = await asyncio.gather(*tasks)

        # Build result dict
        validation_map = dict(zip(urls, results))

        valid_count = sum(validation_map.values())
        log.info(
            "batch_validation_completed",
            total=len(urls),
            valid=valid_count,
            invalid=len(urls) - valid_count,
        )

        return validation_map
