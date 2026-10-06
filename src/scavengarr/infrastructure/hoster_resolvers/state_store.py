"""Keep the hoster resolver registry's state across restarts.

A restart forgot every resolution (cached for an hour), every redirect and
every open circuit breaker: the first requests after a deploy resolved all
their links again and waited for hosters known to be down. The store keeps
one snapshot of them in the cache backend (``CachePort``, diskcache or
Redis), written every 30 s when something changed and at shutdown, and
restores it at the start with every lifetime shortened by the downtime
(openspec persist-resolver-state).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import structlog

from scavengarr.domain.ports.cache import CachePort
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)

log = structlog.get_logger(__name__)

# The key stays when the format changes; the version in the snapshot tells
# one of another format, which is deleted. Raise it with a format change
# that export_state()'s readers cannot take.
_KEY = "hoster_state:v1"
_VERSION = 1
# Resolutions live an hour, cooldowns at most an hour: past a day a
# snapshot holds nothing worth restoring
_TTL_S = 24 * 3600


def _invalid(snapshot: Any) -> str | None:
    """Why *snapshot* cannot be restored; ``None`` when it can."""
    if not isinstance(snapshot, dict):
        return "malformed"
    if snapshot.get("version") != _VERSION:
        return "version"
    if not isinstance(snapshot.get("taken_at"), int | float):
        return "malformed"
    return None


class HosterStateStore:
    """The registry's resolutions and redirects and the circuit breakers
    that are not closed (by kind: ``plugin``, ``hoster``), in the cache."""

    def __init__(
        self,
        cache: CachePort,
        registry: HosterResolverRegistry,
        breakers: Mapping[str, PluginCircuitBreaker],
        *,
        interval_s: float = 30.0,
    ) -> None:
        self._cache = cache
        self._registry = registry
        self._breakers = breakers
        self._interval_s = interval_s
        # The changes the stored snapshot holds
        self._saved = self._changes()
        self._task: asyncio.Task[None] | None = None
        self._restored: dict[str, Any] = {"restored_at": None}

    @property
    def restored(self) -> dict[str, Any]:
        """What the start restored (``/api/v1/stats/metrics``)."""
        return dict(self._restored)

    def _changes(self) -> tuple[int, ...]:
        return (
            self._registry.changes,
            *(breaker.changes for breaker in self._breakers.values()),
        )

    async def restore(self) -> None:
        """Load the snapshot into the registry and the breakers (at the
        start, before the first request).

        A snapshot of another version or one that cannot be read is
        deleted and the start goes on without it.
        """
        try:
            await self._restore()
        except Exception as exc:  # noqa: BLE001  (the state saves work; the start goes on)
            log.warning("hoster_state_restore_failed", error=str(exc))
        # What was restored is what the backend holds
        self._saved = self._changes()

    async def _restore(self) -> None:
        try:
            snapshot = await self._cache.get(_KEY)
        except Exception as exc:  # noqa: BLE001  (unpickling: a cut write)
            await self._discard("unreadable", error=str(exc))
            return
        resolutions = redirects = breakers = 0
        age_s = 0.0
        if snapshot is not None:
            reason = _invalid(snapshot)
            if reason is not None:
                await self._discard(reason)
                return
            # A clock set back (the Pi has none until NTP) counts as no
            # downtime: lifetimes never grow
            age_s = max(0.0, time.time() - snapshot["taken_at"])
            try:
                resolutions, redirects = self._registry.import_state(snapshot, age_s)
                breakers = sum(
                    breaker.import_state(snapshot["breakers"].get(kind, []), age_s)
                    for kind, breaker in self._breakers.items()
                )
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                # The type only: the message can quote a value, a CDN URL
                # with its token
                await self._discard("malformed", error=type(exc).__name__)
                return
        self._restored = {
            "restored_at": datetime.now(UTC).isoformat(),
            "age_s": round(age_s),
            "resolutions": resolutions,
            "redirects": redirects,
            "breakers": breakers,
        }
        log.info(
            "hoster_state_restored",
            age_s=round(age_s),
            resolutions=resolutions,
            redirects=redirects,
            breakers=breakers,
        )

    async def _discard(self, reason: str, error: str = "") -> None:
        log.warning("hoster_state_discarded", reason=reason, error=error)
        await self._cache.delete(_KEY)

    def start(self) -> None:
        """Write the snapshot every *interval_s* while the state changes."""
        self._task = asyncio.create_task(self._run(), name="hoster_state_store")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            await self._save()

    async def aclose(self) -> None:
        """Stop the periodic writes and write the last changes (shutdown,
        before the cache backend closes)."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._save()

    async def _save(self) -> None:
        """Write the snapshot when its content changed since the last
        write; a failed write is tried again at the next one."""
        changes = self._changes()
        if changes == self._saved:
            return
        snapshot = {
            "version": _VERSION,
            "taken_at": time.time(),
            **self._registry.export_state(),
            "breakers": {
                kind: breaker.export_state() for kind, breaker in self._breakers.items()
            },
        }
        try:
            await self._cache.set(_KEY, snapshot, ttl=_TTL_S)
        except Exception as exc:  # noqa: BLE001  (the next write has it all)
            log.warning("hoster_state_save_failed", error=str(exc))
            return
        self._saved = changes
