"""Shared base class for httpx-based Python plugins.

Eliminates boilerplate that is duplicated across 27+ plugins:
client lifecycle, domain verification, cleanup, safe fetch/parse,
and semaphore creation.

This base class lives in the *infrastructure* layer because it depends
on ``httpx`` and ``structlog``.  The *domain* layer only knows
``PluginProtocol``; plugins that inherit from ``HttpxPluginBase``
structurally satisfy that Protocol.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import structlog

from scavengarr.domain.plugins.base import SearchResult

from .constants import (
    DEFAULT_CLIENT_TIMEOUT,
    DEFAULT_DOMAIN_CHECK_TIMEOUT,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_MAX_RESULTS,
    DEFAULT_USER_AGENT,
)


class HttpxPluginBase:
    """Shared base for httpx-based Python plugins.

    Subclasses **must** set:
    - ``name``
    - ``provides`` (``"stream"`` | ``"download"`` | ``"both"``)
    - ``_domains`` (list with at least one domain string)

    Subclasses **must** override:
    - ``search()`` (the abstract stub raises ``NotImplementedError``)

    Subclasses **may** override:
    - ``version``, ``mode``, ``default_language``
    - ``_max_concurrent``, ``_max_results``, ``_timeout``
    - ``_user_agent``
    """

    # --- Must be set by subclass ---
    name: str = ""
    provides: str = "download"

    # --- Overridable defaults ---
    version: str = "1.0.0"
    mode: str = "httpx"
    default_language: str = "de"

    _domains: list[str] = []  # noqa: RUF012  # subclass overrides
    _max_concurrent: int = DEFAULT_MAX_CONCURRENT
    _max_results: int = DEFAULT_MAX_RESULTS
    _timeout: float = DEFAULT_CLIENT_TIMEOUT
    _user_agent: str = DEFAULT_USER_AGENT

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._domain_verified: bool = False
        self.base_url: str = f"https://{self._domains[0]}" if self._domains else ""
        self._log = structlog.get_logger(self.name or __name__)

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create httpx client if not already running."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent},
            )
        return self._client

    async def _verify_domain(self) -> None:
        """Find and cache a working domain from the fallback list."""
        if self._domain_verified or len(self._domains) <= 1:
            self._domain_verified = True
            return

        client = await self._ensure_client()
        for domain in self._domains:
            url = f"https://{domain}/"
            try:
                resp = await client.head(url, timeout=DEFAULT_DOMAIN_CHECK_TIMEOUT)
                if resp.status_code < 400:
                    self.base_url = f"https://{domain}"
                    self._domain_verified = True
                    self._log.info(f"{self.name}_domain_found", domain=domain)
                    return
            except Exception:  # noqa: BLE001
                continue

        # All failed — keep primary
        self.base_url = f"https://{self._domains[0]}"
        self._domain_verified = True
        self._log.warning(
            f"{self.name}_no_domain_reachable",
            fallback=self._domains[0],
        )

    async def cleanup(self) -> None:
        """Close httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._domain_verified = False

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def _safe_fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        context: str = "",
        **kwargs: object,
    ) -> httpx.Response | None:
        """Fetch *url* with structured error logging.

        Returns ``None`` on failure instead of raising.
        """
        client = await self._ensure_client()
        try:
            handler = getattr(client, method.lower(), client.get)
            resp = await handler(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.TimeoutException:
            self._log.warning(
                f"{self.name}_timeout",
                url=url,
                context=context,
            )
        except httpx.HTTPStatusError as exc:
            self._log.warning(
                f"{self.name}_http_error",
                url=url,
                status=exc.response.status_code,
                context=context,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                f"{self.name}_fetch_error",
                url=url,
                error=str(exc),
                context=context,
            )
        return None

    def _safe_parse_json(
        self,
        response: httpx.Response,
        context: str = "",
    ) -> dict | list | None:
        """Parse JSON response with structured error logging."""
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            self._log.warning(
                f"{self.name}_invalid_json",
                url=str(response.url),
                context=context,
            )
            return None

    def _new_semaphore(self) -> asyncio.Semaphore:
        """Create a bounded semaphore for concurrent detail scraping."""
        return asyncio.Semaphore(self._max_concurrent)

    # ------------------------------------------------------------------
    # Abstract search (subclass must implement)
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search the site and return normalised results.

        Subclasses **must** override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.search() not implemented")
