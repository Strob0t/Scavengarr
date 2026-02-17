"""HTTP-based link validator using HEAD requests with GET fallback."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog
from httpx import HTTPError, TimeoutException

if TYPE_CHECKING:
    from httpx import AsyncClient

log = structlog.get_logger(__name__)

# Cache TTLs for validation results
_CACHE_TTL_VALID = 21600  # 6 hours for valid links
_CACHE_TTL_INVALID = 900  # 15 minutes for invalid links


class _ValidationCacheEntry:
    """Time-bounded cache entry for validation results."""

    __slots__ = ("is_valid", "expires_at")

    def __init__(self, is_valid: bool, ttl: int) -> None:
        self.is_valid = is_valid
        self.expires_at = time.monotonic() + ttl

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class HttpLinkValidator:
    """Validates download links via HTTP HEAD with GET fallback.

    Some streaming hosters (veev.to, savefiles.com) return 403 on HEAD
    but 200 on GET. This validator tries HEAD first, then falls back
    to GET on any failure.

    Features:
        - URL deduplication: each unique URL is validated once per batch.
        - Result caching: validation outcomes are cached in-memory with TTL.

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
        self._cache: dict[str, _ValidationCacheEntry] = {}

    async def validate(self, url: str) -> bool:
        """Validate single URL (HEAD first, GET fallback).

        Returns:
            True if reachable (2xx/3xx) via HEAD or GET, False otherwise.
        """
        # Check cache
        cached = self._cache.get(url)
        if cached is not None and not cached.is_expired:
            return cached.is_valid

        async with self._semaphore:
            if await self._try_head(url):
                self._cache[url] = _ValidationCacheEntry(True, _CACHE_TTL_VALID)
                return True
            is_valid = await self._try_get(url)
            ttl = _CACHE_TTL_VALID if is_valid else _CACHE_TTL_INVALID
            self._cache[url] = _ValidationCacheEntry(is_valid, ttl)
            return is_valid

    async def _try_head(self, url: str) -> bool:
        """Try HEAD request. Returns True if 2xx/3xx."""
        try:
            response = await self.http_client.head(
                url,
                timeout=self.timeout,
                follow_redirects=True,
            )
            is_valid = response.status_code < 400

            log.debug(
                "link_head_result",
                url=url,
                status_code=response.status_code,
                valid=is_valid,
            )
            return is_valid

        except TimeoutException:
            log.debug("link_head_timeout", url=url)
            return False
        except HTTPError as e:
            log.debug("link_head_http_error", url=url, error=str(e))
            return False
        except Exception as e:  # noqa: BLE001
            log.debug("link_head_failed", url=url, error=str(e))
            return False

    async def _try_get(self, url: str) -> bool:
        """Try GET request as fallback. Returns True if 2xx/3xx."""
        try:
            response = await self.http_client.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
            )
            is_valid = response.status_code < 400

            log.debug(
                "link_get_fallback_result",
                url=url,
                status_code=response.status_code,
                valid=is_valid,
            )
            return is_valid

        except TimeoutException:
            log.warning("link_validation_timeout", url=url, timeout=self.timeout)
            return False

        except HTTPError as e:
            log.warning("link_validation_http_error", url=url, error=str(e))
            return False

        except Exception as e:  # noqa: BLE001
            log.warning("link_validation_unexpected_error", url=url, error=str(e))
            return False

    async def validate_batch(self, urls: list[str]) -> dict[str, bool]:
        """Validate multiple URLs concurrently with deduplication.

        Each unique URL is validated only once. Duplicate URLs share
        the same validation result.

        Args:
            urls: List of download links (may contain duplicates).

        Returns:
            Dict mapping url -> is_valid.
        """
        if not urls:
            return {}

        # Deduplicate while preserving order; reject non-HTTP strings
        unique_urls = [
            u for u in dict.fromkeys(urls) if u.startswith(("http://", "https://"))
        ]
        dedup_count = len(urls) - len(unique_urls)

        log.info(
            "batch_validation_started",
            total=len(urls),
            unique=len(unique_urls),
            duplicates_skipped=dedup_count,
        )

        # Parallel validation of unique URLs only
        tasks = [self.validate(url) for url in unique_urls]
        results = await asyncio.gather(*tasks)

        # Build result dict from unique results
        unique_map = dict(zip(unique_urls, results))

        # Propagate to all original URLs (including duplicates);
        # non-HTTP strings that were filtered out are marked invalid.
        validation_map = {url: unique_map.get(url, False) for url in urls}

        valid_count = sum(1 for v in unique_map.values() if v)
        log.info(
            "batch_validation_completed",
            total=len(urls),
            unique=len(unique_urls),
            valid=valid_count,
            invalid=len(unique_urls) - valid_count,
        )

        return validation_map
