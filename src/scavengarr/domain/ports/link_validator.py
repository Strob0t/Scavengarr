"""Port for validating download links (streaming/OCH sites)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LinkValidatorPort(Protocol):
    """Validates if download links are reachable/alive.

    Implementations try HEAD first, then fall back to GET on failure.
    Some streaming hosters block HEAD but respond to GET.
    """

    async def validate(self, url: str) -> bool:
        """Check if URL is reachable (HEAD first, GET fallback).

        Args:
            url: Download link to validate.

        Returns:
            True if URL returns 2xx/3xx on HEAD or GET, False otherwise.
        """
        ...

    async def validate_batch(self, urls: list[str]) -> dict[str, bool]:
        """Validate multiple URLs concurrently.

        Args:
            urls: List of download links.

        Returns:
            Dict mapping url -> is_valid (True/False).
        """
        ...
