"""External challenge solver (Byparr / FlareSolverr) as a browser fetcher.

Both speak the FlareSolverr v1 API::

    POST {base_url}/v1 {"cmd": "request.get", "url": ..., "maxTimeout": ms}
    → {"status": "ok", "solution": {"url", "status", "response", ...}}

Optional sidecar (``playwright.solver_url``): used after the own browser
fails, or alone when the own browser fallback is disabled.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from scavengarr.domain.ports.browser_fetcher import (
    BrowserFetcherPort,
    BrowserSession,
    ClickThrough,
)

log = structlog.get_logger(__name__)

# Chromium/Firefox render non-HTML bodies (JSON) inside a bare <pre>
_PRE_ONLY_RE = re.compile(
    r"^\s*<html[^>]*>\s*<head>.*?</head>\s*<body>\s*<pre[^>]*>(.*)</pre>"
    r"\s*(?:<div[^>]*>.*?</div>\s*)?</body>\s*</html>\s*$",
    re.DOTALL | re.IGNORECASE,
)
# Extra time on top of the solver's own budget for the HTTP round trip
_HTTP_MARGIN_S = 10.0


def _unwrap_pre(body: str) -> str:
    match = _PRE_ONLY_RE.match(body)
    return html.unescape(match.group(1)) if match else body


class SolverFetcher:
    """``BrowserFetcherPort`` backed by a FlareSolverr-compatible service."""

    def __init__(self, *, http_client: httpx.AsyncClient, base_url: str) -> None:
        self._http = http_client
        self._endpoint = base_url.rstrip("/") + "/v1"
        # host -> session of the site's latest solution
        self._sessions: dict[str, BrowserSession] = {}

    async def _solve(self, url: str, timeout: float) -> dict[str, Any] | None:
        """Run ``request.get`` and return the solution (``None`` on failure)."""
        try:
            resp = await self._http.post(
                self._endpoint,
                json={
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": int(timeout * 1000),
                },
                timeout=timeout + _HTTP_MARGIN_S,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("solver_unreachable", url=url, error=str(exc))
            return None

        if not isinstance(data, dict):
            log.warning("solver_invalid_response", url=url)
            return None
        solution = data.get("solution")
        if data.get("status") != "ok" or not isinstance(solution, dict):
            log.warning("solver_failed", url=url, message=data.get("message"))
            return None
        self._keep_session(url, solution)
        return solution

    def _keep_session(self, url: str, solution: dict[str, Any]) -> None:
        """Remember the solution's cookies and User-Agent for *url*'s site."""
        cookies = solution.get("cookies")
        user_agent = solution.get("userAgent")
        if not isinstance(cookies, list) or not isinstance(user_agent, str):
            return
        jar = {
            c["name"]: c["value"]
            for c in cookies
            if isinstance(c, dict)
            and isinstance(c.get("name"), str)
            and isinstance(c.get("value"), str)
        }
        if jar:
            host = urlsplit(url).hostname or ""
            self._sessions[host] = BrowserSession(cookies=jar, user_agent=user_agent)

    async def session(self, url: str) -> BrowserSession | None:
        return self._sessions.get(urlsplit(url).hostname or "")

    async def fetch_text(self, url: str, *, timeout: float) -> str | None:
        solution = await self._solve(url, timeout)
        if solution is None:
            return None
        status = solution.get("status")
        body = solution.get("response")
        if not isinstance(status, int) or status >= 400 or not isinstance(body, str):
            log.warning("solver_http_error", url=url, status=status)
            return None
        return _unwrap_pre(body)

    async def resolve_redirect(self, url: str, *, timeout: float) -> str | None:
        solution = await self._solve(url, timeout)
        target = solution.get("url") if solution else None
        if not isinstance(target, str) or urlsplit(target).netloc in (
            "",
            urlsplit(url).netloc,
        ):
            return None
        return target

    async def click_through(
        self, page_url: str, selector: str, *, timeout: float
    ) -> ClickThrough | None:
        """Not supported: the FlareSolverr API only loads URLs."""
        return None


class ChainedBrowserFetcher:
    """Try several fetchers in order; the first non-``None`` answer wins."""

    def __init__(self, fetchers: Sequence[BrowserFetcherPort]) -> None:
        self._fetchers = tuple(fetchers)
        # host -> the fetcher whose browser last passed that site
        self._served: dict[str, BrowserFetcherPort] = {}

    async def fetch_text(self, url: str, *, timeout: float) -> str | None:
        for fetcher in self._fetchers:
            body = await fetcher.fetch_text(url, timeout=timeout)
            if body is not None:
                self._served[urlsplit(url).hostname or ""] = fetcher
                return body
        return None

    async def session(self, url: str) -> BrowserSession | None:
        """The session of the fetcher that last fetched *url*'s site."""
        fetcher = self._served.get(urlsplit(url).hostname or "")
        return await fetcher.session(url) if fetcher is not None else None

    async def resolve_redirect(self, url: str, *, timeout: float) -> str | None:
        for fetcher in self._fetchers:
            target = await fetcher.resolve_redirect(url, timeout=timeout)
            if target is not None:
                return target
        return None

    async def click_through(
        self, page_url: str, selector: str, *, timeout: float
    ) -> ClickThrough | None:
        for fetcher in self._fetchers:
            result = await fetcher.click_through(page_url, selector, timeout=timeout)
            if result is not None:
                return result
        return None
