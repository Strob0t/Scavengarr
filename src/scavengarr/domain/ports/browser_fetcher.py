"""Port for fetching pages that plain HTTP clients cannot pass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ClickThrough:
    """Where a click on a page's link-out led, and the site's session after it."""

    url: str  # off-site URL the link-out redirected to
    cookies: dict[str, str] = field(default_factory=dict)  # the site's cookies


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

    async def click_through(
        self, page_url: str, selector: str, *, timeout: float
    ) -> ClickThrough | None:
        """Click a link-out on *page_url* and return where it led.

        Opens *page_url*, clicks the first element matching *selector* and
        waits for a navigation that a same-site URL redirects off-site (the
        link-out, e.g. in the page's player iframe). A Turnstile widget the
        click brings up (sites that gate link-outs) is passed: ticked when it
        does not clear by itself, then its form is submitted. Returns the
        target with the site's cookies, whose session may then skip the gate;
        None when nothing left the site within *timeout* seconds.
        """
        ...
