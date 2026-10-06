"""Port for fetching pages that plain HTTP clients cannot pass."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

# What a browser page is wanted for: playback (a stream resolved again at
# play time), a plugin's page, a hoster capture for an answer, or work for
# later requests (background resolutions)
PageKind = Literal["play", "plugin", "capture", "background"]


@dataclass(frozen=True)
class ClickThrough:
    """Where a click on a page's link-out led, and the site's session after it."""

    url: str  # off-site URL the link-out redirected to
    cookies: dict[str, str] = field(default_factory=dict)  # the site's cookies


@dataclass(frozen=True)
class BrowserSession:
    """A site's session in the browser: what an HTTP client needs to reuse it.

    Anti-bot checks (Cloudflare's ``cf_clearance``, HostAdmin's WAF ticket)
    accept their cookie from another client when it sends the same
    User-Agent as the browser that passed the check.
    """

    cookies: dict[str, str]
    user_agent: str


@dataclass(frozen=True, slots=True)
class PageClaim:
    """Whom browser work serves and when it is due (``time.monotonic()``).

    The browser hands out its pages by the claim of the work that asks.
    """

    kind: PageKind
    due: float


# The claim of the browser work the running code starts: set by the
# application around a search or a resolution, inherited by the tasks it
# starts (plugins and resolvers sit between it and the browser)
page_claim: ContextVar[PageClaim | None] = ContextVar("page_claim", default=None)


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

    async def session(self, url: str) -> BrowserSession | None:
        """Return the browser's session for *url*'s site.

        Asked after ``fetch_text()`` passed the site's challenge, so plain
        HTTP requests can go on with it. None when the browser holds no
        cookies for the site.
        """
        ...
