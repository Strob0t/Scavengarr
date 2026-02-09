"""Port for validating download links (streaming/OCH sites)."""

from __future__ import annotations

from typing import Protocol


class LinkValidatorPort(Protocol):
    """Validates if download links are reachable/alive.

    Implementations check HTTP status codes (HEAD requests).
    """

    async def validate(self, url: str) -> bool:
        """Check if URL is reachable.

        Args:
            url: Download link to validate.

        Returns:
            True if URL returns 2xx/3xx, False if 4xx/5xx/timeout.
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
