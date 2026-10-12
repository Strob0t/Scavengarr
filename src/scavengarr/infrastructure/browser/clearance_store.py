"""Keep bot-challenge clearance cookies and capture states across restarts.

Cloudflare (``cf_clearance``), DDoS-Guard (``__ddg*``) and the HostAdmin WAF
(``ha-waf-*``) grant a solved challenge as a cookie. Browser contexts live in
memory, so a restart used to cost one challenge per protected host. The store
keeps just these cookies in the cache (``CachePort``, diskcache or Redis) until
they expire and puts them into every new browser context
(docs/plans/captcha-solving.md, decision B3).

The cookies stay valid only for the same browser (User-Agent/TLS) and IP, and
for ~20–45 min; a stale one merely costs a new challenge.

A capture hoster's player keeps what lets it skip its own challenge in its
host's cookies, localStorage and IndexedDB (Filemoon's captcha, Dropload's
Turnstile widget). After a capture found its stream the stealth pool keeps
that state per host (``keep_capture_state()``, at most 24 h), and each new
stealth context starts from it and the clearance cookies
(``creation_state()``; localStorage and IndexedDB can be set only when a
context is created). docs/plans/challenge-reactions.md, R4.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit

import structlog

from scavengarr.domain.ports.cache import CachePort

if TYPE_CHECKING:
    from patchright.async_api import StorageState

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
# The capture states: host -> {saved_at, cookies, origins}
_STATE_KEY = "browser:capture_state:v1"
_STATE_TTL_S = 24 * 3600
_MAX_STATE_BYTES = 512 * 1024


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


def _for_host(cookie: Mapping[str, Any], host: str) -> bool:
    """Whether *cookie*'s domain is *host* or a parent domain of it."""
    domain = str(cookie.get("domain", "")).lstrip(".").lower()
    return bool(domain) and (host == domain or host.endswith(f".{domain}"))


def _on_host(origin: Mapping[str, Any], host: str) -> bool:
    return (urlsplit(str(origin.get("origin", ""))).hostname or "") == host


def _usable(cookie: Mapping[str, Any], now: float) -> bool:
    """A session cookie, or one that has not expired."""
    expires = cookie.get("expires")
    if not isinstance(expires, (int, float)) or expires <= 0:
        return True
    return expires > now + _MIN_REMAINING_S


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

    async def _load_states(self, now: float) -> dict[str, dict[str, Any]]:
        """The kept capture states younger than 24 h; a corrupt key is
        dropped."""
        raw = await self._cache.get(_STATE_KEY)
        if not raw:
            return {}
        try:
            states = json.loads(raw)
            if not isinstance(states, dict):
                raise ValueError("not an object")
        except (TypeError, ValueError):
            log.warning("capture_state_corrupt")
            await self._cache.delete(_STATE_KEY)
            return {}
        return {
            host: entry
            for host, entry in states.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("saved_at"), (int, float))
            and entry["saved_at"] > now - _STATE_TTL_S
        }

    async def keep_capture_state(
        self, hosts: Iterable[str], state: Mapping[str, Any]
    ) -> None:
        """Keep *state*'s part of each of *hosts*: the cookies of the host
        and its parent domains, the origins on it (a context's
        ``storage_state()``)."""
        now = time.time()
        cookies, origins = state.get("cookies") or [], state.get("origins") or []
        kept: dict[str, dict[str, Any]] = {}
        sizes = 0
        for host in hosts:
            entry = {
                "saved_at": now,
                "cookies": [c for c in cookies if _for_host(c, host)],
                "origins": [o for o in origins if _on_host(o, host)],
            }
            if not entry["cookies"] and not entry["origins"]:
                continue
            size = len(json.dumps(entry).encode())
            if size > _MAX_STATE_BYTES:
                log.warning("capture_state_too_large", host=host, bytes=size)
                continue
            kept[host] = entry
            sizes += size
        if not kept:
            return
        async with self._lock:
            states = await self._load_states(now)
            states.update(kept)
            await self._cache.set(_STATE_KEY, json.dumps(states), ttl=_STATE_TTL_S)
        log.info(
            "capture_state_saved",
            hosts=sorted(kept),
            cookies=sum(len(e["cookies"]) for e in kept.values()),
            origins=sum(len(e["origins"]) for e in kept.values()),
            bytes=sizes,
        )

    async def creation_state(self) -> StorageState | None:
        """The storage state a new stealth context starts from: the
        unexpired clearance cookies and every kept host's cookies and
        origins, the newest winning per cookie and per origin; None when
        there is nothing."""
        try:
            now = time.time()
            cookies = {_key(c): c for c in await self._load() if _alive(c, now)}
            origins: dict[str, dict[str, Any]] = {}
            states = await self._load_states(now)
            # Oldest first: a newer entry's cookie or origin replaces it
            for _, entry in sorted(states.items(), key=lambda kv: kv[1]["saved_at"]):
                for cookie in entry.get("cookies") or []:
                    if isinstance(cookie, dict) and _usable(cookie, now):
                        cookies[_key(cookie)] = cookie
                for origin in entry.get("origins") or []:
                    if isinstance(origin, dict) and origin.get("origin"):
                        origins[str(origin["origin"])] = origin
        except Exception as exc:  # noqa: BLE001  (a missing state costs a challenge)
            log.warning("capture_state_restore_failed", error=type(exc).__name__)
            return None
        if not cookies and not origins:
            return None
        if states:
            log.info("capture_state_restored", hosts=sorted(states))
        # The entries are storage_state() output, read back from the cache
        state = {"cookies": list(cookies.values()), "origins": list(origins.values())}
        return cast("StorageState", state)
