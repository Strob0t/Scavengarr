"""Port for fetching pages that plain HTTP clients cannot pass."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrowserFetcherPort(Protocol):
    """Fetches a URL through a real browser (e.g. behind Cloudflare Turnstile).

    Used as a fallback by HTTP-based plugins when a site answers them with
    an anti-bot challenge.
    """

    async def fetch_text(self, url: str, *, timeout: float) -> str | None:
        """Return the response body of *url* (HTML or raw text such as JSON).

        Returns None when the page could not be loaded or the challenge was
        not solved within *timeout* seconds.
        """
        ...

    async def resolve_redirect(self, url: str, *, timeout: float) -> str | None:
        """Return the first off-site URL that *url* redirects to.

        For link-out endpoints (e.g. ``/external/<hash>`` → hoster or link
        container). The target page itself is not loaded. Returns None when
        *url* does not leave its host within *timeout* seconds.
        """
        ...
