"""Keep bot-challenge clearance cookies across restarts.

Cloudflare (``cf_clearance``), DDoS-Guard (``__ddg*``) and the HostAdmin WAF
(``ha-waf-*``) grant a solved challenge as a cookie. Browser contexts live in
memory, so a restart used to cost one challenge per protected host. The store
keeps just these cookies in the cache (``CachePort``, diskcache or Redis) until
they expire and puts them into every new browser context
(docs/plans/captcha-solving.md, decision B3).

The cookies stay valid only for the same browser (User-Agent/TLS) and IP, and
for ~20–45 min; a stale one merely costs a new challenge.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol

import structlog

from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

_CACHE_KEY = "browser:clearance_cookies"
_MAX_TTL_S = 24 * 3600
# Cookies that expire within this margin are not worth keeping
_MIN_REMAINING_S = 60
# Fields accepted by BrowserContext.add_cookies()
_COOKIE_FIELDS = (
    "name",
    "value",
    "domain",
    "path",
    "expires",
    "httpOnly",
    "secure",
    "sameSite",
)


class _CookieJar(Protocol):
    """The part of a Patchright ``BrowserContext`` the store uses."""

    async def cookies(self) -> Any: ...
    async def add_cookies(self, cookies: Any) -> None: ...


def _is_clearance(cookie: dict[str, Any]) -> bool:
    name = str(cookie.get("name", ""))
    # ha-waf-*: HostAdmin WAF ticket (kinoger), valid for 30 min
    return name == "cf_clearance" or name.startswith(("__ddg", "ha-waf-"))


def _alive(cookie: dict[str, Any], now: float) -> bool:
    expires = cookie.get("expires")
    return isinstance(expires, (int, float)) and expires > now + _MIN_REMAINING_S


def _key(cookie: dict[str, Any]) -> tuple[str, str, str]:
    return (cookie["name"], cookie["domain"], cookie.get("path", "/"))


class ClearanceStore:
    """Clearance cookies in the cache, shared by all browser contexts."""

    def __init__(self, cache: CachePort) -> None:
        self._cache = cache
        self._lock = asyncio.Lock()
        self._last_saved: frozenset[tuple[str, str, str, str]] = frozenset()

    async def _load(self) -> list[dict[str, Any]]:
        raw = await self._cache.get(_CACHE_KEY)
        if not raw:
            return []
        try:
            cookies = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("clearance_cache_corrupt")
            return []
        return (
            [c for c in cookies if isinstance(c, dict)]
            if isinstance(cookies, list)
            else []
        )

    async def restore(self, context: _CookieJar) -> None:
        """Add the unexpired stored clearance cookies to *context*."""
        try:
            now = time.time()
            cookies = [c for c in await self._load() if _alive(c, now)]
            if cookies:
                await context.add_cookies(cookies)
                log.info(
                    "clearance_restored",
                    domains=sorted({c["domain"] for c in cookies}),
                )
        except Exception as exc:  # noqa: BLE001  (a missing cookie costs a challenge)
            log.warning("clearance_restore_failed", error=str(exc))

    async def remember(self, context: _CookieJar) -> None:
        """Store *context*'s clearance cookies (merged with the stored ones)."""
        try:
            now = time.time()
            fresh = [
                {k: c[k] for k in _COOKIE_FIELDS if k in c}
                for c in await context.cookies()
                if _is_clearance(c) and _alive(c, now)
            ]
            snapshot = frozenset((*_key(c), str(c["value"])) for c in fresh)
            if not fresh or snapshot <= self._last_saved:
                return
            async with self._lock:
                merged = {_key(c): c for c in await self._load() if _alive(c, now)}
                merged.update({_key(c): c for c in fresh})
                cookies = list(merged.values())
                ttl = min(_MAX_TTL_S, int(max(c["expires"] for c in cookies) - now))
                await self._cache.set(_CACHE_KEY, json.dumps(cookies), ttl=ttl)
                self._last_saved = frozenset(
                    (*_key(c), str(c["value"])) for c in cookies
                )
            log.info("clearance_saved", domains=sorted({c["domain"] for c in fresh}))
        except Exception as exc:  # noqa: BLE001  (losing it costs a challenge)
            log.warning("clearance_save_failed", error=str(exc))
