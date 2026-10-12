"""The plugins' long-term record in the cache backend.

A site that died shows as months of searches without a result and checks
that found it unreachable (megakino_to and movie4k: 30 unreachable marks
each in the sixth end-to-end round); whether such a plugin stays, is
disabled by default or is removed is the maintainer's decision from this
record. ``PluginSearchRunner`` counts searches, results, timeouts and the
results the episode filter dropped, ``PluginHealthMonitor`` checks and
unreachable marks, per plugin and UTC day. The record is one value
``plugin_history:v1`` in the cache backend (diskcache or Redis), written
every minute when it changed and at shutdown with the days older than 180
dropped, restored at the start, like ``HosterStateStore``;
``GET /api/v1/stats/plugins`` reports it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

from scavengarr.domain.ports.cache import CachePort
from scavengarr.domain.ports.plugin_history import PluginCounter

log = structlog.get_logger(__name__)

# The key stays when the format changes; the version in the record tells
# one of another format, which is deleted
_KEY = "plugin_history:v1"
_VERSION = 1
# The record's window; the unreachable shares are over these windows
WINDOW_DAYS = 180
SHARE_WINDOWS = (30, 90, 180)
_INTERVAL_S = 60.0
# Every write renews the lifetime: the record goes only after a year
# without the app
_TTL_S = 366 * 24 * 3600

# plugin -> ISO day -> counter -> count
Record = dict[str, dict[str, dict[str, int]]]


def _invalid(snapshot: Any) -> str | None:
    """Why *snapshot* cannot be restored; ``None`` when it can."""
    if not isinstance(snapshot, dict):
        return "malformed"
    if snapshot.get("version") != _VERSION:
        return "version"
    plugins = snapshot.get("plugins")
    if not isinstance(plugins, dict):
        return "malformed"
    for days in plugins.values():
        if not isinstance(days, dict):
            return "malformed"
        for day, counters in days.items():
            if not isinstance(day, str) or not isinstance(counters, dict):
                return "malformed"
            if not all(
                isinstance(k, str) and isinstance(v, int) for k, v in counters.items()
            ):
                return "malformed"
    return None


class PluginHistory:
    """Counters per plugin and UTC day, kept in the cache backend."""

    def __init__(
        self,
        cache: CachePort,
        *,
        interval_s: float = _INTERVAL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cache = cache
        self._interval_s = interval_s
        self._clock = clock
        self._plugins: Record = {}
        # Counted since the start, and how much of it the backend holds
        self._changes = 0
        self._saved = 0
        self._task: asyncio.Task[None] | None = None

    def _today(self) -> date:
        return datetime.fromtimestamp(self._clock(), UTC).date()

    def count(self, plugin: str, counter: PluginCounter, n: int = 1) -> None:
        """Add *n* to *plugin*'s *counter* of today."""
        day = self._plugins.setdefault(plugin, {}).setdefault(
            self._today().isoformat(), {}
        )
        day[counter] = day.get(counter, 0) + n
        self._changes += 1

    async def restore(self) -> None:
        """Load the record (at the start, before the first request).

        A record of another version or one that cannot be read is deleted
        and the start goes on without it.
        """
        try:
            await self._restore()
        except Exception as exc:  # noqa: BLE001  (the record is evidence; the start goes on)
            log.warning("plugin_history_restore_failed", error=str(exc))
        self._saved = self._changes

    async def _restore(self) -> None:
        try:
            snapshot = await self._cache.get(_KEY)
        except Exception as exc:  # noqa: BLE001  (unpickling: a cut write)
            await self._discard("unreadable", error=str(exc))
            return
        if snapshot is None:
            log.info("plugin_history_restored", plugins=0, days=0)
            return
        reason = _invalid(snapshot)
        if reason is not None:
            await self._discard(reason)
            return
        self._plugins = snapshot["plugins"]
        self._trim()
        log.info(
            "plugin_history_restored",
            plugins=len(self._plugins),
            days=sum(len(days) for days in self._plugins.values()),
        )

    async def _discard(self, reason: str, error: str = "") -> None:
        log.warning("plugin_history_discarded", reason=reason, error=error)
        await self._cache.delete(_KEY)

    def start(self) -> None:
        """Write the record every *interval_s* while it changes."""
        self._task = asyncio.create_task(self._run(), name="plugin_history")

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
        """Write the record when it changed since the last write; a failed
        write is tried again at the next one."""
        changes = self._changes
        if changes == self._saved:
            return
        self._trim()
        try:
            await self._cache.set(
                _KEY, {"version": _VERSION, "plugins": self._plugins}, ttl=_TTL_S
            )
        except Exception as exc:  # noqa: BLE001  (the next write has it all)
            log.warning("plugin_history_save_failed", error=str(exc))
            return
        self._saved = changes

    def _trim(self) -> None:
        """Drop the days outside the window and the plugins without a day."""
        first = self._first_day(WINDOW_DAYS)
        for plugin, days in list(self._plugins.items()):
            for day in [d for d in days if d < first]:
                del days[day]
            if not days:
                del self._plugins[plugin]

    def _first_day(self, window: int) -> str:
        """The first ISO day of the last *window* days, today included."""
        return (self._today() - timedelta(days=window - 1)).isoformat()

    def report(self) -> dict[str, Any]:
        """The record with, per plugin, its last day with a result and the
        share of its checks that found the site unreachable over the last
        30, 90 and 180 days (``None`` without a check)."""
        self._trim()
        plugins: dict[str, Any] = {}
        for name, days in sorted(self._plugins.items()):
            with_results = [d for d, c in days.items() if c.get("results", 0) > 0]
            plugins[name] = {
                "last_result_day": max(with_results, default=None),
                "unreachable_share": {
                    str(window): _share(days, self._first_day(window))
                    for window in SHARE_WINDOWS
                },
                "days": {day: dict(days[day]) for day in sorted(days)},
            }
        return {
            "today": self._today().isoformat(),
            "window_days": WINDOW_DAYS,
            "plugins": plugins,
        }


def _share(days: dict[str, dict[str, int]], first: str) -> float | None:
    """Unreachable checks over all checks from *first* on."""
    checks = unreachable = 0
    for day, counters in days.items():
        if day >= first:
            checks += counters.get("checks", 0)
            unreachable += counters.get("unreachable", 0)
    return round(unreachable / checks, 4) if checks else None
