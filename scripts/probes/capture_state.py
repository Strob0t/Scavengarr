"""The browser's kept state: capture states and clearance cookies.

``probe capture_state`` prints per kept host its age, each cookie's name,
domain, path and remaining lifetime, and per origin the localStorage key
names with their value lengths; then the stored clearance cookies alike.
Read-only; prints names, lengths and times, never a value.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from scavengarr.infrastructure.browser.clearance_store import _CACHE_KEY, _STATE_KEY
from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.config import load_config


def span(seconds: float) -> str:
    """*seconds* as the largest fitting units: ``45 s``, ``18 min``,
    ``2 h 18 min``, ``364 d``."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        hours, minutes = divmod(seconds // 60, 60)
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{seconds // 86400} d"


def _cookie_line(cookie: Mapping[str, Any], now: float) -> str:
    expires = cookie.get("expires")
    if not isinstance(expires, (int, float)) or expires <= 0:
        lifetime = "session"
    elif expires <= now:
        lifetime = f"expired {span(now - expires)} ago"
    else:
        lifetime = f"{span(expires - now)} left"
    return (
        f"- cookie {cookie.get('name')} | domain {cookie.get('domain')}"
        f" | path {cookie.get('path')} | {lifetime}"
    )


def _origin_line(origin: Mapping[str, Any]) -> str:
    address = str(origin.get("origin", ""))
    host = urlsplit(address).hostname or address
    parts = []
    items = [i for i in origin.get("localStorage") or [] if isinstance(i, dict)]
    if items:
        keys = ", ".join(
            f"{i.get('name')} ({len(str(i.get('value', '')))} chars)" for i in items
        )
        parts.append(f"localStorage {keys}")
    bases = [b for b in origin.get("indexedDB") or [] if isinstance(b, dict)]
    if bases:
        parts.append("IndexedDB " + ", ".join(str(b.get("name")) for b in bases))
    return f"- origin {host}: {'; '.join(parts) or 'empty'}"


def _decode(raw: str | bytes | None, kind: type) -> Any:
    """The key's JSON as *kind*; ``None`` if missing, ``...`` if corrupt."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return ...
    return value if isinstance(value, kind) else ...


def _state_lines(raw: str | bytes | None, now: float) -> list[str]:
    title = f"capture states ({_STATE_KEY})"
    states = _decode(raw, dict)
    if states is None:
        return [f"{title}: none"]
    if states is ...:
        return [f"{title}: corrupt"]
    lines = [f"{title}: {len(states)} hosts"]
    for host, entry in states.items():
        if not isinstance(entry, dict):
            lines.append(f"host {host}: corrupt")
            continue
        saved_at = entry.get("saved_at")
        if isinstance(saved_at, (int, float)):
            kept = datetime.fromtimestamp(saved_at, UTC).strftime("%H:%M:%S UTC")
            lines.append(f"host {host}: kept {kept}, {span(now - saved_at)} ago")
        else:
            lines.append(f"host {host}: kept at an unknown time")
        cookies = [c for c in entry.get("cookies") or [] if isinstance(c, dict)]
        origins = [o for o in entry.get("origins") or [] if isinstance(o, dict)]
        lines += [_cookie_line(c, now) for c in cookies]
        lines += [_origin_line(o) for o in origins]
        if not cookies and not origins:
            lines.append("- nothing kept")
    return lines


def _clearance_lines(raw: str | bytes | None, now: float) -> list[str]:
    title = f"clearance cookies ({_CACHE_KEY})"
    cookies = _decode(raw, list)
    if cookies is None:
        return [f"{title}: none"]
    if cookies is ...:
        return [f"{title}: corrupt"]
    cookies = [c for c in cookies if isinstance(c, dict)]
    return [f"{title}: {len(cookies)}"] + [_cookie_line(c, now) for c in cookies]


def report_lines(
    states: str | bytes | None, clearance: str | bytes | None, *, now: float
) -> list[str]:
    """The report of the two keys' raw values at *now*."""
    return _state_lines(states, now) + _clearance_lines(clearance, now)


async def main() -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
    )
    async with cache:
        states = await cache.get(_STATE_KEY)
        clearance = await cache.get(_CACHE_KEY)
    for line in report_lines(states, clearance, now=time.time()):
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
